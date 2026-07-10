"""Orchestrateur d'enrichissement contact (waterfall, gratuit).

Pour un établissement : OSM (tags directs) -> si un site est trouvé, on le
scrape pour combler email / instagram / facebook / téléphone manquants.
Ne remplit que les champs vides. Fail-soft de bout en bout.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date
from typing import Optional

from .osm import lookup_osm
from .places import lookup_places
from .siret_matcher import _name_overlap, _tokens as _distinctive_tokens
from .url_filter import clean_website
from .website_scraper import scrape_contacts

# Au-delà de ce nombre d'avis, un établissement est manifestement ANCIEN : une
# fiche « création récente / ouverture prochaine » (ou dont l'activité a démarré
# il y a moins de ~90 j) ne peut pas les porter -> le candidat Places est le
# VOISIN, pas l'enseigne cherchée (cas SOKA FOOD, 3 semaines, capté sur Bolkiri
# et ses 218 avis, même rue).
REVIEW_FRESHNESS_CONFLICT = 100
_FRESH_SIGNALS = {"création récente", "ouverture prochaine"}
_FRESH_MAX_AGE_DAYS = 90


def _fresh_review_conflict(
    main_signal: Optional[str],
    activity_start_date: Optional[date],
    review_count: Optional[int],
) -> bool:
    """Détecteur de contradiction fraîcheur (cas SOKA FOOD / Bolkiri, Saint-Gratien
    95210). Une enseigne qui vient d'être créée (signal « création récente » /
    « ouverture prochaine », ou activité démarrée il y a < ~90 j) NE PEUT PAS
    afficher des centaines d'avis anciens : si le candidat Places en porte
    beaucoup (>= seuil), c'est le voisin qui a été capté (SOKA FOOD n'existe pas
    encore sur Places -> searchText renvoie Bolkiri et ses 218 avis). On rejette
    le match, MÊME si un token de nom concorde par ailleurs."""
    if review_count is None or review_count < REVIEW_FRESHNESS_CONFLICT:
        return False
    if main_signal in _FRESH_SIGNALS:
        return True
    if activity_start_date is not None:
        # Future (pré-ouverture) => days négatif => récent ; passé récent => < 90.
        return (date.today() - activity_start_date).days < _FRESH_MAX_AGE_DAYS
    return False


def _strong_name_match(query: Optional[str], candidate: Optional[str]) -> bool:
    """Concordance de nom FORTE entre l'enseigne cherchée et le nom d'un résultat
    Places/OSM. Réutilise la tokenisation distinctive du matcher SIREN
    (`siret_matcher._tokens` : minuscule, sans accents, sans mots génériques
    'cafe/bar/restaurant/le/la…'). FORTE = l'un des jeux de tokens distinctifs
    est inclus dans l'autre (pas un simple token commun). Ainsi « Marco Del
    Caffé » {marco,del,caffe} et « Café Marco Polo » {marco,polo} NE concordent
    PAS (un seul token commun, aucun sous-ensemble) -> on n'écrit rien ; tandis
    que « Giorgina » {giorgina} concorde avec « Giorgina Ristorante »
    {giorgina,ristorante}. Précision d'abord : un champ vide vaut mieux qu'un
    faux (contact d'un homonyme envoyé au prospect)."""
    a, b = _distinctive_tokens(query), _distinctive_tokens(candidate)
    if not a or not b:
        return False
    return a.issubset(b) or b.issubset(a)


@dataclass
class ContactInfo:
    phone: Optional[str] = None
    email: Optional[str] = None
    website: Optional[str] = None
    instagram: Optional[str] = None
    facebook: Optional[str] = None
    review_count: Optional[int] = None  # nb d'avis Places -> proxy de fraîcheur
    match_basis: Optional[str] = None  # 'geo' | 'text' | None -> pilote la confiance
    place_name: Optional[str] = None  # displayName Places -> concordance de nom

    def has_priority(self) -> bool:
        """A-t-on au moins un des champs prioritaires (email/tel/insta) ?"""
        return bool(self.email or self.phone or self.instagram)


class ContactEnricher:
    def __init__(self, osm_delay: float = 1.0):
        self.osm_delay = osm_delay  # Overpass est rate-limité : on reste poli.

    def enrich(
        self,
        name: str,
        latitude: Optional[float],
        longitude: Optional[float],
        website: Optional[str] = None,
        city: Optional[str] = None,
        postal: Optional[str] = None,
        main_signal: Optional[str] = None,
        activity_start_date: Optional[date] = None,
    ) -> ContactInfo:
        info = ContactInfo(website=clean_website(website))

        # 0. Google Places (si clé) — tête de waterfall : tel + site, bonne
        #    couverture des lieux physiques. Match validé en amont (places.py) :
        #    par distance au point Sirene (lat/lon) si dispo, sinon par texte.
        #    VERROU D'IDENTITÉ (cause n°1 de l'audit) : la proximité confirme un
        #    LIEU, pas une IDENTITÉ. On n'écrit RIEN d'un match Places que si :
        #      - géo-confirmé (match_basis='geo') ET au moins un token distinctif
        #        commun entre l'enseigne cherchée et le displayName Places (le
        #        mono-token reste accepté ici : enseignes courtes MOKA-style) ;
        #        ZÉRO recoupement -> rejet (cas SOKA FOOD géo-confirmé sur les
        #        données de Bolkiri, même rue à Saint-Gratien) ;
        #      - OU nom concordant FORTEMENT (homonyme du repli texte rejeté :
        #        Peace Museum->Café de la Paix, Marco Del Caffé->Café Marco Polo).
        #    ET, dans tous les cas, PAS de contradiction de fraîcheur (une
        #    création récente n'a pas des centaines d'avis anciens).
        places = lookup_places(name, city=city, postal=postal, lat=latitude, lon=longitude)
        display = places.get("display_name")
        identity_ok = (
            places.get("match_basis") == "geo" and _name_overlap(name, display or "")
        ) or _strong_name_match(name, display)
        if (
            places.get("matched")
            and identity_ok
            and not _fresh_review_conflict(
                main_signal, activity_start_date, places.get("review_count")
            )
        ):
            info.phone = info.phone or places.get("phone")
            info.website = info.website or clean_website(places.get("website"))
            info.review_count = places.get("review_count")
            info.match_basis = places.get("match_basis")
            info.place_name = display

        # 1. OSM (tags directs) si on a des coordonnées. Même verrou : le nœud
        #    OSM est déjà borné géographiquement (rayon 150 m autour du point du
        #    lead), mais sa sélection interne n'exige qu'un token commun -> on
        #    exige en plus une concordance de nom FORTE avant d'accepter QUOI QUE
        #    CE SOIT de ce nœud (sinon rien : commerce voisin homonyme).
        if latitude is not None and longitude is not None:
            osm = lookup_osm(name, latitude, longitude)
            if _strong_name_match(name, osm.get("name")):
                info.phone = info.phone or osm.get("phone")
                info.website = info.website or clean_website(osm.get("website"))
                info.instagram = info.instagram or osm.get("instagram")
                info.email = info.email or osm.get("email")
                info.facebook = info.facebook or osm.get("facebook")
            time.sleep(self.osm_delay)

        # 2. Scrape du site (le pilier pour email / Instagram).
        if info.website and not (info.email and info.instagram and info.phone):
            scraped = scrape_contacts(info.website)
            info.email = info.email or scraped.get("email")
            info.instagram = info.instagram or scraped.get("instagram")
            info.facebook = info.facebook or scraped.get("facebook")
            info.phone = info.phone or scraped.get("phone")

        return info
