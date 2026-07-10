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

FRONTIÈRE PRESCRIPTEUR/EXÉCUTANT (itération precision-archi-1, grounded sur 5 faux
positifs mesurés) : le vocabulaire « agencement / conception / design » aspirait
les EXÉCUTANTS (menuisiers, fabricants de mobilier, cuisinistes-poseurs,
carreleurs, mandataires immobiliers). Règle : un métier d'exécution DANS
L'IDENTITÉ (nom/handle) écarte le compte MÊME avec un titre archi en bio
(agenceurmenuisier.fr : bio « Architecte d'Intérieur » mais handle
« agenceurmenuisier » = menuisier-agenceur). Idem un COMMERCE d'ameublement
auto-déclaré (« magasin d'ameublement/de meubles/boutique de décoration ») dans la
bio/catégorie : design-build qui vend/fabrique ce qu'il pose (grounded
bontemps.esquisse), écarté MÊME avec titre archi. En simple MENTION (post/bio d'un
studio à titre archi) -> on laisse le juge trancher (zelee_design_studio parle de
« fabriqué et posé par l'Atelier Franchini » mais reste un studio prescripteur).
Cas ancrés hors_cible : endora.studio3d (cours privés), habiteretgrandir (coach),
atelierlesimple (menuiserie), cotefauteuils (tapissier), agenceurmenuisier.fr
(menuisier), sartorius_mobilier (fabricant mobilier), rekto_agencement
(cuisiniste-poseur), pensart.bzh (carreleur/béton ciré), lys_brocque_archi_immo
(mandataire immobilier)."""
from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

# Formation / coaching VERS d'autres pros (B2B2B) : le compte vend du savoir, pas
# des projets clients. Grounded : endora (« cours privés »), habiteretgrandir
# (« coach HOMER® »). Word-boundary pour éviter « transformation » -> « formation ».
_FORMATION_KW = ("coach", "coaching", "cours prive", "cours prives", "formation",
                 "masterclass", "mentorat", "e-learning", "e learning", "apprendre le")

# Métiers d'EXÉCUTION (artisan / fabricant / poseur) VOISINS : fournisseurs, pas
# prescripteurs. Grounded : atelierlesimple (menuiserie/ébénisterie), cotefauteuils
# (tapissier), schmidt_cambrai (« 1er fabricant français »), pensart.bzh (carrelage/
# béton ciré). En bio/nom SANS titre archi -> hors_cible.
_EXEC_TRADE_KW = ("menuiserie", "menuisier", "ebenisterie", "ebeniste", "tapissier",
                  "tapisserie", "serrurier", "marbrier", "ferronnier", "fabricant",
                  "carreleur", "carrelage", "cuisiniste", "beton cire", "poseur")

# Sous-ensemble des métiers d'exécution qui, présents dans l'IDENTITÉ (nom + handle),
# écartent le compte MÊME avec un titre archi en bio : un studio prescripteur ne
# porte JAMAIS un métier de fabrication/pose dans son nom ou son @handle (grounded
# agenceurmenuisier.fr). Tokens longs (>=8 car.) -> recherche EN SOUS-CHAÎNE sûre
# sur le handle collé (« agenceurmenuisier » contient « menuisier »).
_EXEC_IDENTITY_KW = ("menuiserie", "menuisier", "ebenisterie", "ebeniste", "tapissier",
                     "marbrier", "ferronnier", "serrurier", "fabricant", "carreleur",
                     "carrelage", "cuisiniste")

# Franchises / réseaux de cuisine (magasin de réseau, PAS studio indépendant).
# Grounded schmidt_cambrai (déjà pris par « fabricant » ; ajouté par sûreté).
_CUISINE_FRANCHISE_KW = ("schmidt", "mobalpa", "cuisinella", "ixina", "socoo",
                         "cuisine plus", "arthur bonnet", "you cuisines", "cuisines references")

# Commerce d'ameublement/déco AUTO-DÉCLARÉ dans l'IDENTITÉ (bio/catégorie) : un
# « magasin d'ameublement / de meubles / boutique de décoration » vend le mobilier
# qu'il présente — il est le COMMERCE, pas le prescripteur qui spécifie à un tiers.
# Garde DUR : le titre « architecte d'intérieur » en bio ne NEUTRALISE PAS ce cas
# (design-build : conçoit ET vend/fabrique ce qu'il pose). Grounded bontemps.esquisse
# (bio « Designer & architecte d'intérieur / Magasin d'ameublement et décoration »).
# Discriminant DÉTERMINISTE vs zelee_design_studio (« Boutique Concept Store » +
# archi, fabrication SOUS-TRAITÉE) : zelee ne se déclare PAS « magasin/boutique
# d'ameublement/de meubles/de décoration » -> épargné, laissé au juge. Phrases
# contiguës uniquement (pas « décoration » seul, présent chez des décoratrices
# légitimes almonainterieurs/lydie.cuminetti/...).
_FURNITURE_STORE_KW = ("magasin d'ameublement", "magasin de meubles", "magasin de meuble",
                       "magasin de decoration", "magasin de deco",
                       "boutique d'ameublement", "boutique de meubles",
                       "boutique de decoration", "boutique de meuble")

# Titre archi/design d'intérieur : sa présence NEUTRALISE les gardes SOUPLES (métier
# en bio, marque de mobilier, cuisiniste, immobilier) — un studio qui parle de
# « menuiserie sur-mesure » n'est pas un menuisier. NE neutralise PAS le garde DUR
# _EXEC_IDENTITY (métier d'exécution dans le nom/handle).
_ARCHI_TITLE_KW = ("architecte d'interieur", "architecte dinterieur",
                   "architectes d'interieur", "architecture interieure",
                   "interior design", "interior architect", "designer d'interieur",
                   "design d'interieur")

# Prestataire de contenu / média / non-lieu (pas un studio d'archi).
_NON_PRESCRIBER_KW = ("graphiste", "webdesign", "web design", "ux/ui", "ux ui",
                      "community manager", "photographe", "webmagazine",
                      "motion design", "illustrateur")

# Termes « poseur cuisine-SdB-dressing » : >=2 dans la bio SANS titre archi = agenceur-
# poseur (cuisiniste), pas prescripteur (grounded rekto_agencement « Cuisine - Salle
# de bain - Dressing »). Seuil >=2 pour épargner un archi qui rénove UNE salle de bain.
_FITTER_KW = ("cuisine", "salle de bain", "sdb", "dressing")

# Domaines étrangers (piège CHR connu ; garde léger, aucun cas dans l'échantillon).
# On compare le VRAI ccTLD (dernier label de l'hôte), jamais une sous-chaîne : sinon
# « caroline-studio.fr » (.ca), « benjamin….fr » (.be), « behance.net » (.be) seraient
# faussement écartés — trahison directe de l'objectif VOLUME MAX national.
_FOREIGN_TLD = frozenset({"be", "ch", "ca", "lu"})


def _norm(text: Optional[str]) -> str:
    # NFKC D'ABORD (comme siret_matcher.clean_name) : les lettres stylisées Insta
    # (ex. bio en Unicode mathématique italique « É𝘣é𝘯𝘪𝘴𝘵𝘦𝘳𝘪𝘦 ») se décomposent en
    # ASCII normal via la compatibilité NFKC ; NFD seul les laisse intactes et les
    # gardes mots-clés (artisan/formation/…) les ratent silencieusement (grounded
    # jks_ebenistes, annotation navigateur T6). NFD ensuite pour retirer les accents.
    text = unicodedata.normalize("NFKC", text or "").lower()
    return "".join(c for c in unicodedata.normalize("NFD", text)
                   if unicodedata.category(c) != "Mn")


def _haystack(profile: Dict[str, Any]) -> str:
    """Bio + nom + catégorie business, normalisés (sans accent)."""
    return _norm(" \n ".join([
        profile.get("biography") or "", profile.get("fullName") or "",
        profile.get("businessCategoryName") or "",
    ]))


def _identity(profile: Dict[str, Any]) -> str:
    """Identité auto-affichée = nom + handle, normalisés. Le handle est inclus
    COLLÉ (pour la sous-chaîne « agenceurmenuisier » -> « menuisier ») ET éclaté
    sur « . » / « _ » (pour le match par token « immo », « mobilier »)."""
    name = profile.get("fullName") or ""
    user = profile.get("username") or ""
    user_split = re.sub(r"[._]+", " ", user)
    return _norm(" ".join([name, user, user_split]))


def _kw_present(hay: str, keywords) -> bool:
    """Mot-clé présent en frontière de mot (évite les sous-chaînes parasites)."""
    return any(re.search(r"(?<![a-z])" + re.escape(k) + r"(?![a-z])", hay) for k in keywords)


def _has_formation_cue(profile: Dict[str, Any]) -> bool:
    return _kw_present(_haystack(profile), _FORMATION_KW)


def _has_archi_title(profile: Dict[str, Any]) -> bool:
    return _kw_present(_haystack(profile), _ARCHI_TITLE_KW)


def _has_artisan_metier(profile: Dict[str, Any]) -> bool:
    return _kw_present(_haystack(profile), _EXEC_TRADE_KW)


def _exec_in_identity(profile: Dict[str, Any]) -> bool:
    """Métier d'exécution DANS le nom/handle (sous-chaîne sûre, tokens longs).
    Écarte MÊME avec titre archi (agenceurmenuisier.fr)."""
    ident = _identity(profile)
    return any(k in ident for k in _EXEC_IDENTITY_KW)


def _furniture_store_identity(profile: Dict[str, Any]) -> bool:
    """« magasin/boutique d'ameublement / de meubles / de décoration » AUTO-DÉCLARÉ
    dans la bio/catégorie = commerce d'ameublement, PAS prescripteur — écarte MÊME
    avec titre archi (design-build, grounded bontemps.esquisse). Phrases contiguës
    (n'attrape pas « décoration » isolé chez les décoratrices prescriptrices)."""
    return _kw_present(_haystack(profile), _FURNITURE_STORE_KW)


def _mobilier_brand_identity(profile: Dict[str, Any]) -> bool:
    """« mobilier » dans le NOM/handle = marque/fabricant de mobilier
    (sartorius_mobilier), PAS un simple service listé en bio (espacesprojets
    « bureaux & mobilier » reste studio_actif)."""
    return _kw_present(_identity(profile), ("mobilier", "meuble", "meubles"))


def _fitter_combo(profile: Dict[str, Any]) -> bool:
    """Cuisiniste/poseur : >=2 termes cuisine-SdB-dressing en bio/nom (rekto)."""
    hay = _haystack(profile)
    return sum(1 for k in _FITTER_KW if _kw_present(hay, (k,))) >= 2


def _cuisine_franchise(profile: Dict[str, Any]) -> bool:
    return _kw_present(_identity(profile) + " " + _haystack(profile), _CUISINE_FRANCHISE_KW)


def _eshop_mobilier(profile: Dict[str, Any]) -> bool:
    hay = _haystack(profile) + " " + _identity(profile)
    return _kw_present(hay, ("e-shop", "eshop", "e shop", "boutique de mobilier",
                             "boutique deco", "boutique de decoration", "vente de mobilier"))


def _is_immo(profile: Dict[str, Any]) -> bool:
    """Mandataire / agent immobilier (identité dominante immobilier), grounded
    lys_brocque_archi_immo (bio « Immobilier », nom/handle « immo »)."""
    return (_kw_present(_identity(profile), ("immo", "immobilier"))
            or _kw_present(_haystack(profile), ("immobilier", "mandataire", "agent immobilier")))


def _is_non_prescriber(profile: Dict[str, Any]) -> bool:
    return _kw_present(_haystack(profile), _NON_PRESCRIBER_KW)


def _url_cctld(url: str) -> Optional[str]:
    """Dernier label de l'hôte d'une URL (ex. « https://studio.be/x » -> « be »).
    Renvoie None si l'hôte est absent. Tolère l'absence de schéma et un port."""
    url = (url or "").strip().lower()
    if not url:
        return None
    if "//" not in url:
        url = "//" + url  # urlsplit exige un « // » pour peupler netloc.
    host = urlsplit(url).netloc.split("@")[-1].split(":")[0].rstrip(".")
    if not host or "." not in host:
        return None
    return host.rsplit(".", 1)[-1]


def _is_foreign(profile: Dict[str, Any]) -> bool:
    urls = [profile.get("externalUrl") or ""]
    urls += [(e.get("url") or "") for e in (profile.get("externalUrls") or [])]
    return any(_url_cctld(u) in _FOREIGN_TLD for u in urls)


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
    Ordre : compte mort -> noise ; formation/coaching -> hors_cible (même avec
    titre archi) ; prestataire/média -> hors_cible ; métier d'exécution DANS
    L'IDENTITÉ -> hors_cible (même avec titre archi) ; métier d'exécution en
    bio SANS titre archi -> hors_cible ; frontière produit SANS titre archi
    (marque de mobilier, cuisiniste, franchise cuisine, e-shop, immobilier) ->
    hors_cible ; étranger -> hors_cible ; sinon None (le juge tranche actif/
    dormant/perso). AUCUN verdict studio_* déterministe (leçon sonde : titre
    insuffisant)."""
    today = today or date.today()
    if _is_dead_account(profile):
        return "noise"
    if _has_formation_cue(profile):
        return "hors_cible"
    if _is_non_prescriber(profile):
        return "hors_cible"
    # Garde DUR : métier d'exécution dans le nom/handle -> le titre archi en bio
    # ne rachète pas un menuisier (agenceurmenuisier.fr).
    if _exec_in_identity(profile):
        return "hors_cible"
    # Garde DUR : commerce d'ameublement/déco auto-déclaré (« magasin d'ameublement »)
    # -> le titre archi ne rachète pas un magasin (design-build, bontemps.esquisse).
    if _furniture_store_identity(profile):
        return "hors_cible"
    # Gardes SOUPLES : neutralisés par un titre archi/décorateur d'intérieur (un
    # studio qui MENTIONNE menuiserie/mobilier/cuisine d'un projet reste au juge).
    if not _has_archi_title(profile):
        if _has_artisan_metier(profile):
            return "hors_cible"
        if _mobilier_brand_identity(profile):
            return "hors_cible"
        if _fitter_combo(profile):
            return "hors_cible"
        if _cuisine_franchise(profile):
            return "hors_cible"
        if _eshop_mobilier(profile):
            return "hors_cible"
        if _is_immo(profile):
            return "hors_cible"
    if _is_foreign(profile):
        return "hors_cible"
    return None
