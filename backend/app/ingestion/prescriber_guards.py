"""Garde-fous déterministes du profil ARCHITECTE (A1, avant tout LLM).

Fonctions PURES : à partir d'un profil brut (profile scraper Apify), renvoient un
verdict déterministe `hors_cible`/`noise` ou None (le compte descend au juge
`judge_prescripteur`). Gratuit et reproductible : attrape l'ÉVIDENT non-cible
(coach/formation, artisan/fabricant voisin, prestataire de contenu, étranger,
compte mort) sans dépenser de crédit LLM.

LEÇON DE LA SONDE (non négociable) : le titre « architecte d'intérieur » seul NE
SUFFIT PAS à trancher `studio_actif` (divnaanni le porte mais est compte_perso ;
habiteretgrandir le porte mais est un coach). On ne fait donc AUCUN verdict
`studio_actif`/`studio_dormant`/`compte_perso` déterministe — seul le juge, avec
la récence et la cadence PRÉCALCULÉES, distingue actif/dormant/perso.
Cas ancrés hors_cible : endora.studio3d (cours privés), habiteretgrandir (coach),
atelierlesimple (menuiserie), cotefauteuils (tapissier)."""
from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Any, Dict, Optional

# Formation / coaching VERS d'autres pros (B2B2B) : le compte vend du savoir, pas
# des projets clients. Grounded : endora (« cours privés »), habiteretgrandir
# (« coach HOMER® »). Word-boundary pour éviter « transformation » -> « formation ».
_FORMATION_KW = ("coach", "coaching", "cours prive", "cours prives", "formation",
                 "masterclass", "mentorat", "e-learning", "e learning", "apprendre le")

# Métiers d'artisan / fabricant VOISINS (fournisseurs, pas prescripteurs). Grounded :
# atelierlesimple (menuiserie/ébénisterie), cotefauteuils (tapissier).
_ARTISAN_KW = ("menuiserie", "menuisier", "ebenisterie", "ebeniste", "tapissier",
               "tapisserie", "serrurier", "marbrier", "ferronnier",
               "fabricant de meubles", "fabrication de meubles")

# Titre archi/design d'intérieur : sa présence NEUTRALISE le garde artisan (un
# studio qui parle de « menuiserie sur-mesure » n'est pas un menuisier).
_ARCHI_TITLE_KW = ("architecte d'interieur", "architecte dinterieur",
                   "architectes d'interieur", "architecture interieure",
                   "interior design", "interior architect", "designer d'interieur",
                   "design d'interieur")

# Prestataire de contenu / média / non-lieu (pas un studio d'archi).
_NON_PRESCRIBER_KW = ("graphiste", "webdesign", "web design", "ux/ui", "ux ui",
                      "community manager", "photographe", "webmagazine",
                      "motion design", "illustrateur")

# Domaines étrangers (piège CHR connu ; garde léger, aucun cas dans l'échantillon).
_FOREIGN_TLD = (".be", ".ch", ".ca", ".lu")


def _norm(text: Optional[str]) -> str:
    text = (text or "").lower()
    return "".join(c for c in unicodedata.normalize("NFD", text)
                   if unicodedata.category(c) != "Mn")


def _haystack(profile: Dict[str, Any]) -> str:
    """Bio + nom + catégorie business, normalisés (sans accent)."""
    return _norm(" \n ".join([
        profile.get("biography") or "", profile.get("fullName") or "",
        profile.get("businessCategoryName") or "",
    ]))


def _kw_present(hay: str, keywords) -> bool:
    """Mot-clé présent en frontière de mot (évite les sous-chaînes parasites)."""
    return any(re.search(r"(?<![a-z])" + re.escape(k) + r"(?![a-z])", hay) for k in keywords)


def _has_formation_cue(profile: Dict[str, Any]) -> bool:
    return _kw_present(_haystack(profile), _FORMATION_KW)


def _has_archi_title(profile: Dict[str, Any]) -> bool:
    return _kw_present(_haystack(profile), _ARCHI_TITLE_KW)


def _has_artisan_metier(profile: Dict[str, Any]) -> bool:
    return _kw_present(_haystack(profile), _ARTISAN_KW)


def _is_non_prescriber(profile: Dict[str, Any]) -> bool:
    return _kw_present(_haystack(profile), _NON_PRESCRIBER_KW)


def _is_foreign(profile: Dict[str, Any]) -> bool:
    urls = [profile.get("externalUrl") or ""]
    urls += [(e.get("url") or "") for e in (profile.get("externalUrls") or [])]
    hay = " ".join(urls).lower()
    return any(tld in hay for tld in _FOREIGN_TLD)


def _is_dead_account(profile: Dict[str, Any]) -> bool:
    """Compte quasi mort = bruit : <=2 posts, <=5 abonnés, bio quasi vide."""
    posts = profile.get("postsCount")
    followers = profile.get("followersCount")
    if not isinstance(posts, int) or not isinstance(followers, int):
        return False
    if posts > 2 or followers > 5:
        return False
    return len(_norm(profile.get("biography") or "").strip()) <= 5


def guard_prescripteur(profile: Dict[str, Any], today: Optional[date] = None) -> Optional[str]:
    """Verdict déterministe du profil archi, ou None (à confier au juge).
    Ordre : compte mort -> noise ; formation/coaching -> hors_cible (même avec titre
    archi : un coach vend du savoir) ; prestataire/média -> hors_cible ; artisan
    SANS titre archi -> hors_cible ; étranger -> hors_cible ; sinon None (le juge
    tranche actif/dormant/perso, avec récence/cadence précalculées).
    AUCUN verdict studio_* déterministe (leçon sonde : titre insuffisant)."""
    today = today or date.today()
    if _is_dead_account(profile):
        return "noise"
    if _has_formation_cue(profile):
        return "hors_cible"
    if _is_non_prescriber(profile):
        return "hors_cible"
    if _has_artisan_metier(profile) and not _has_archi_title(profile):
        return "hors_cible"
    if _is_foreign(profile):
        return "hors_cible"
    return None
