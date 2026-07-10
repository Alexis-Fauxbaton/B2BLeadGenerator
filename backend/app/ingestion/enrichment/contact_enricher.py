"""Orchestrateur d'enrichissement contact (waterfall, gratuit).

Pour un établissement : OSM (tags directs) -> si un site est trouvé, on le
scrape pour combler email / instagram / facebook / téléphone manquants.
Ne remplit que les champs vides. Fail-soft de bout en bout.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from .osm import lookup_osm
from .places import lookup_places
from .siret_matcher import _tokens as _distinctive_tokens
from .url_filter import clean_website
from .website_scraper import scrape_contacts


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
    ) -> ContactInfo:
        info = ContactInfo(website=clean_website(website))

        # 0. Google Places (si clé) — tête de waterfall : tel + site, bonne
        #    couverture des lieux physiques. Match validé en amont (places.py) :
        #    par distance au point Sirene (lat/lon) si dispo, sinon par texte.
        #    VERROU D'IDENTITÉ (cause n°1 de l'audit) : on n'écrit RIEN d'un
        #    match Places que si celui-ci est géo-confirmé (match_basis='geo')
        #    OU si le nom du lieu concorde FORTEMENT avec l'enseigne. Sinon
        #    (homonyme accepté par le repli texte : Peace Museum->Café de la
        #    Paix, Marco Del Caffé->Café Marco Polo) -> vide plutôt qu'un faux.
        places = lookup_places(name, city=city, postal=postal, lat=latitude, lon=longitude)
        if places.get("matched") and (
            places.get("match_basis") == "geo"
            or _strong_name_match(name, places.get("display_name"))
        ):
            info.phone = info.phone or places.get("phone")
            info.website = info.website or clean_website(places.get("website"))
            info.review_count = places.get("review_count")
            info.match_basis = places.get("match_basis")
            info.place_name = places.get("display_name")

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
