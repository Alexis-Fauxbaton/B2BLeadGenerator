"""Garde-fous déterministes du profil Insta (brique 3, avant tout LLM).

Fonctions PURES : à partir d'un profil brut (sortie du profile scraper Apify),
renvoient un verdict déterministe (`established`/`chain_multisite`) ou None si
aucun signal certain — le compte descend alors au juge LLM (`judge_dossier`).
Gratuit et reproductible : attrape l'évident (chaînes multi-adresses, gros
volume de posts, historique long, horaires/résa affichés) sans dépenser de
crédit LLM. Cas de non-régression : MOKA (3 adresses en bio + 'open everyday')
meurt ici en chain_multisite.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from typing import Any, Dict, List, Optional

# Volume de posts au-delà duquel un compte est clairement établi (Le Palais=200).
POSTS_ESTABLISHED_HARD = 150

# Hébergeurs de réservation en ligne = établissement en exploitation.
_RESA_HOSTS = ("zenchef", "thefork", "lafourchette", "sevenrooms", "opentable",
               "resy", "newtable")

# Réservation : mot-clé (normalisé, sans accent) + téléphone FR / URL = en service.
_RESA_KW = "reserv"  # réservation / réserver / réservez
_PHONE_RE = re.compile(r"\b0\s?\d(?:[\s.\-]?\d\d){4}\b")   # 01 43 25 87 99, 0143258799…
_URL_RE = re.compile(r"https?://|www\.", re.IGNORECASE)

# Indices de PRÉ-OUVERTURE (normalisés, sans accent). Leur présence en bio OU
# dans une légende récente INTERDIT tout verdict « established » tiré d'une
# simple mention de réservation : une pré-ouverture ouvre souvent la résa en
# ligne AVANT d'ouvrir ses portes (ex. villa.henriette « Ouverture 10 Juillet »,
# « OPENING SOON »). Sans ce garde, un vrai lead `opening_soon` serait tué au
# garde avant d'atteindre le juge — régression de rappel sur le signal privilégié.
_OPENING_CUES = ("ouverture", "on ouvre", "ouvre bientot", "opening soon",
                 "openingsoon", "coming soon", "comingsoon", "bientot",
                 "prochainement")

# Villes connues (multi-sites en bio). Volontairement des grandes villes non
# ambiguës — évite de compter un simple gentilé comme une 2e adresse.
_CITY_TOKENS = ("paris", "lyon", "marseille", "bordeaux", "lille", "toulouse",
                "nantes", "nice", "strasbourg", "montpellier", "rennes", "cannes")

# Horaires : plages "10h-18h" / "10:30-23:00" / "10h30-23h00" / "de 10h à 19h".
# On N'ACCEPTE PAS un "Nh" isolé (ex. "ouverture dans 48h" = compte à rebours de
# pré-ouverture, PAS des horaires) : seuls les mots-clés et les VRAIES plages
# comptent (leçon de revue : le bare-\d{1,2}h sur-étiquetait des pré-ouvertures).
_RANGE_RE = re.compile(r"\d{1,2}\s?[:h]\s?\d{0,2}\s*[-–—à]\s*\d{1,2}\s?[:h]\s?\d{0,2}")
_HOURS_KW = (
    "ouvert du", "ouvert 7", "ouvert tous les jours", "tous les jours",
    "open everyday", "open every day", "7j/7", "7/7", "midi et soir",
)
_POSTAL_RE = re.compile(r"\b\d{5}\b")
_PIN = "\U0001F4CD"  # 📍

# Mois / saisons / année : un segment de ligne pin qui est une DATE d'ouverture
# ("Juillet 2026", "Printemps/Été 2026") n'est PAS une 2e adresse.
_MONTHS = ("janvier", "fevrier", "mars", "avril", "mai", "juin", "juillet",
           "aout", "septembre", "octobre", "novembre", "decembre")
_SEASONS = ("printemps", "ete", "automne", "hiver")
_YEAR_RE = re.compile(r"\b20\d\d\b")


def _norm(text: Optional[str]) -> str:
    text = (text or "").lower()
    return "".join(c for c in unicodedata.normalize("NFD", text)
                   if unicodedata.category(c) != "Mn")


def _has_hours_in_bio(bio: Optional[str]) -> bool:
    """Horaires d'ouverture affichés = lieu en exploitation. Mots-clés OU vraie
    plage horaire uniquement — un "Nh" isolé (compte à rebours de pré-ouverture,
    ex. "ouverture dans 48h") ne compte PAS (évite un faux `established`)."""
    if not bio:
        return False
    t = _norm(bio)
    if any(kw in t for kw in _HOURS_KW):
        return True
    return bool(_RANGE_RE.search(bio))


def _has_reservation_link(profile: Dict[str, Any]) -> bool:
    """Lien de réservation (bio ou externalUrl(s)) = établissement en service."""
    parts: List[str] = [
        profile.get("externalUrl") or "",
        profile.get("biography") or "",
    ]
    for e in profile.get("externalUrls") or []:
        parts.append((e.get("url") or ""))
    hay = " ".join(parts).lower()
    return any(host in hay for host in _RESA_HOSTS)


def _has_reservation_in_bio(bio: Optional[str]) -> bool:
    """Réservation active DANS LA BIO : mot-clé 'réserv…' + numéro de téléphone.
    Signal fort d'exploitation (une pré-ouverture n'affiche pas de ligne de résa).
    Cas ancré : osabaita ('Réservation : 01 43 25 87 99')."""
    if not bio:
        return False
    return _RESA_KW in _norm(bio) and bool(_PHONE_RE.search(bio))


def _has_opening_cue(profile: Dict[str, Any]) -> bool:
    """True si la bio OU une des ~12 dernières légendes annonce une (pré-)ouverture.
    Sert de veto au verdict `established` déterministe tiré d'une résa (une résa
    en ligne peut être teasée avant l'ouverture)."""
    texts = [profile.get("biography") or ""]
    texts += [(x.get("caption") or "") for x in (profile.get("latestPosts") or [])[:12]]
    joined = _norm(" \n ".join(texts))
    return any(cue in joined for cue in _OPENING_CUES)


def _has_reservation_in_posts(profile: Dict[str, Any]) -> bool:
    """Un post récent appelle à RÉSERVER via un site (réserv… + URL) ET le profil
    ne porte AUCUN indice de pré-ouverture (bio/légendes, cf. `_has_opening_cue`)
    = établissement DÉJÀ en service.

    Le veto pré-ouverture est IMPÉRATIF (ne PAS le retirer) : une pré-ouverture
    ouvre fréquemment la réservation en ligne avant d'ouvrir ses portes ; sans ce
    garde, ce helper capturerait un vrai `opening_soon` et le tuerait AVANT le
    juge — l'exact opposé du garde-fou absolu « recall opening ».

    NB : villa.henriette_cabourg — le cas qui avait motivé ce helper — est en
    réalité une PRÉ-OUVERTURE (bio « Ouverture 10 Juillet 2026 », « OPENING
    SOON », post « réservations sur notre site www.villa-henriette.fr »). Elle
    n'est donc **volontairement PAS** captée ici et retombe au juge. Ce helper ne
    capte plus que de vraies résas d'établissements déjà ouverts (test synthétique
    + régression de non-capture d'une pré-ouverture)."""
    if _has_opening_cue(profile):
        return False
    for x in (profile.get("latestPosts") or [])[:12]:
        cap = x.get("caption") or ""
        if _RESA_KW in _norm(cap) and _URL_RE.search(cap):
            return True
    return False


def _multi_city_in_bio(bio: Optional[str]) -> bool:
    """≥2 villes connues distinctes listées sur une MÊME ligne de bio (séparateur
    virgule / pipe / •) = marque multi-sites. Cas ancré : cherescousinesbagels
    ('Lyon 6, Paris 11'). Restreint aux lignes EN LISTE pour éviter les faux
    positifs (une phrase mentionnant deux villes n'est pas une liste d'adresses)."""
    if not bio:
        return False
    for line in bio.splitlines():
        if not any(sep in line for sep in (",", "|", "•")):
            continue
        t = _norm(line)
        cities = {c for c in _CITY_TOKENS if re.search(r"\b" + c + r"\b", t)}
        if len(cities) >= 2:
            return True
    return False


def _is_date_segment(seg: str) -> bool:
    """True si un segment de ligne pin est une DATE d'ouverture (mois, saison ou
    année) plutôt qu'une adresse — ex. 'Juillet 2026', 'Printemps/Été 2026'.
    Évite de compter un mois d'ouverture comme une 2e adresse (faux
    chain_multisite : cas chezgratien '📍 Villeneuve d'Aveyron | Juillet 2026')."""
    if _YEAR_RE.search(seg):
        return True
    t = _norm(seg)
    return any(tok in t for tok in _MONTHS + _SEASONS)


def _count_addresses_in_bio(bio: Optional[str]) -> int:
    """Nombre d'adresses distinctes déclarées en bio. Deux signaux :
    - codes postaux distincts (\\b\\d{5}\\b) ;
    - liste de lieux marquée par un pin 📍 et séparée par | ou • (cas MOKA :
      '📍Champs Elysées | Opéra | Galeries Lafayette' -> 3). Les segments qui sont
      une date d'ouverture (mois/saison/année) sont EXCLUS : une ligne pin comme
      '📍 Villeneuve d'Aveyron | Juillet 2026' compte 1 adresse, pas 2.
    Renvoie le maximum des deux comptes."""
    if not bio:
        return 0
    postals = len(set(_POSTAL_RE.findall(bio)))
    pin_max = 0
    for line in bio.splitlines():
        if _PIN in line:
            segs = [s for s in re.split(r"[|•]", line.replace(_PIN, ""))
                    if s.strip() and not _is_date_segment(s)]
            pin_max = max(pin_max, len(segs))
    return max(postals, pin_max)


def _long_history(profile: Dict[str, Any], today: date, threshold_days: int = 150) -> bool:
    """True si l'exploitation dure depuis des mois (plusieurs posts anciens) =>
    établi. Reprend la logique de _profile_long_history (migrée depuis
    instagram.py). Robuste : exige PLUSIEURS posts vieux (pas un throwback isolé)."""
    dates: List[date] = []
    for x in profile.get("latestPosts") or []:
        ts = (x.get("timestamp") or "")[:10]
        try:
            dates.append(datetime.strptime(ts, "%Y-%m-%d").date())
        except ValueError:
            continue
    if not dates:
        return False
    old = [d for d in dates if (today - d).days > threshold_days]
    return len(old) >= min(3, len(dates))


def guard_verdict(profile: Dict[str, Any], today: Optional[date] = None) -> Optional[str]:
    """Verdict déterministe du profil, ou None (à confier au juge LLM).
    Ordre : multi-adresses / multi-villes -> chain_multisite ; sinon volume /
    historique / horaires / résa (lien, bio, posts) -> established ; sinon None."""
    today = today or date.today()
    bio = profile.get("biography") or ""
    if _count_addresses_in_bio(bio) >= 2 or _multi_city_in_bio(bio):
        return "chain_multisite"
    posts_count = profile.get("postsCount")
    if isinstance(posts_count, int) and posts_count > POSTS_ESTABLISHED_HARD:
        return "established"
    if _long_history(profile, today):
        return "established"
    if _has_hours_in_bio(bio):
        return "established"
    if _has_reservation_link(profile):
        return "established"
    if _has_reservation_in_bio(bio):
        return "established"
    if _has_reservation_in_posts(profile):
        return "established"
    return None
