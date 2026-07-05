"""Matching lead Insta -> SIREN/SIRET via le registre (brique 1 du pivot).

Remplace `backfill_siren`. Chaîne : nom nettoyé (auto-accept seulement si
cohérence géo) -> adresse (BAN -> /near_point) -> arbitre LLM sur candidats
ambigus. JAMAIS de merge nom-seul sans géo ni arbitre (piège Auréa : un
"AUREA" 56.10A existe à Théoule, la bio "bijoux, Portugal" doit le rejeter).
Fail-soft partout. Cf. docs/inventory-pivot-design.md.
"""
from __future__ import annotations

import re
import time
import unicodedata
from typing import Any, Callable, Dict, List, Optional

import requests

from .naf_classifier import classify_naf

# Mots génériques ignorés pour la concordance de nom (repris de backfill).
_GENERIC = {
    "le", "la", "les", "du", "de", "des", "et", "aux", "au", "chez", "paris",
    "cafe", "bar", "restaurant", "brasserie", "hotel", "resto", "bistro",
    "bistrot", "traiteur", "pizzeria", "boulangerie", "snack", "food", "coffee",
    "shop", "coffeeshop", "salon", "the",
}

# Séparateurs de décoration dans les fullName Insta ("NOM • VILLE", "NOM - VILLE").
_SEP_RE = re.compile(r"[|•\n–]| - |(?<=\w)-(?=[A-ZÀ-Ý])")
_NUM_RE = re.compile(r"\b(\d{1,4})\b")


def clean_name(raw: Optional[str]) -> str:
    """Nom Insta -> nom cherchable : NFKC (lettres stylisées -> ASCII), strip
    emojis/symboles, premier segment avant séparateur décoratif."""
    text = unicodedata.normalize("NFKC", raw or "")
    # S* = symboles/emojis, C* = contrôles, Mn = variation selectors (U+FE0F
    # après un emoji) — les accents français sont composés par NFKC, donc
    # retirer Mn ne les casse pas.
    text = "".join(c for c in text
                   if unicodedata.category(c)[0] not in ("S", "C")
                   and unicodedata.category(c) != "Mn")
    first = _SEP_RE.split(text)[0]
    return re.sub(r"\s+", " ", first).strip()


def street_number(address: Optional[str]) -> Optional[str]:
    """Premier numéro de voie d'une adresse (clé de comparaison ±exacte)."""
    m = _NUM_RE.search(address or "")
    return m.group(1) if m else None


def _tokens(text: Optional[str]) -> set:
    text = (text or "").lower()
    text = "".join(c for c in unicodedata.normalize("NFD", text)
                   if unicodedata.category(c) != "Mn")
    return {t for t in re.split(r"[^a-z0-9]+", text)
            if len(t) > 1 and t not in _GENERIC and not t.isdigit()}


def _name_overlap(ig_name: str, sirene_text: str) -> bool:
    """Au moins un token distinctif du nom Insta présent côté Sirene."""
    want = _tokens(ig_name)
    return bool(want) and bool(want & _tokens(sirene_text))


SEARCH_URL = "https://recherche-entreprises.api.gouv.fr/search"
NEAR_URL = "https://recherche-entreprises.api.gouv.fr/near_point"
BAN_URL = "https://api-adresse.data.gouv.fr/search/"

Fetch = Callable[[str, Dict[str, Any]], Dict[str, Any]]

_MIN_INTERVAL = 0.15  # recherche-entreprises : 7 req/s max
_last_call = [0.0]


def _http_get(url: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """GET throttlé, fail-soft {} (convention enrichisseurs)."""
    wait = _MIN_INTERVAL - (time.monotonic() - _last_call[0])
    if wait > 0:
        time.sleep(wait)
    _last_call[0] = time.monotonic()
    try:
        resp = requests.get(url, params=params, timeout=15,
                            headers={"User-Agent": "chr-signal-radar"})
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return {}


def _candidates(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Résultats API -> candidats plats. Pour /near_point l'établissement
    proche est dans matching_etablissements (prime sur le siège)."""
    out: List[Dict[str, Any]] = []
    for res in results or []:
        etab = (res.get("matching_etablissements") or [None])[0] or res.get("siege") or {}
        out.append({
            "siren": res.get("siren"),
            "siret": etab.get("siret"),
            "nom": res.get("nom_complet") or res.get("nom_raison_sociale") or "",
            "enseignes": " ".join(etab.get("liste_enseignes") or []),
            "naf": etab.get("activite_principale") or res.get("activite_principale"),
            "adresse": etab.get("adresse") or "",
            "cp": etab.get("code_postal") or "",
            "date_creation": etab.get("date_creation") or res.get("date_creation"),
        })
    return out


def search_by_name(name: str, city: Optional[str], postal: Optional[str],
                   fetch: Fetch) -> List[Dict[str, Any]]:
    """Recherche par nom nettoyé (+ ville dans q, + code_postal si connu)."""
    q = " ".join(filter(None, [clean_name(name), city]))
    if not q:
        return []
    params: Dict[str, Any] = {"q": q, "per_page": 5}
    if postal:
        params["code_postal"] = postal
    data = fetch(SEARCH_URL, params)
    return _candidates(data.get("results") or [])


def _geo_consistent(cand: Dict[str, Any], city: Optional[str],
                    postal: Optional[str]) -> bool:
    """Cohérence géo REQUISE pour l'auto-accept nom : CP concordant, ou nom de
    ville présent dans l'adresse Sirene. Aucune géo connue -> False (arbitre)."""
    if postal and cand["cp"].startswith(postal[:2]):
        return True
    if city:
        c = _tokens(city)
        return bool(c) and bool(c & _tokens(cand["adresse"]))
    return False


def pick_by_name(cands: List[Dict[str, Any]], name: str,
                 city: Optional[str], postal: Optional[str]) -> Optional[Dict[str, Any]]:
    """Sélection PURE par nom : NAF CHR + token distinctif commun + géo
    cohérente. Sans géo -> None (jamais de merge nom-seul)."""
    for cand in cands:
        if not (cand["naf"] and classify_naf(cand["naf"])):
            continue
        if not _name_overlap(name, f'{cand["nom"]} {cand["enseignes"]}'):
            continue
        if _geo_consistent(cand, city, postal):
            return cand
    return None
