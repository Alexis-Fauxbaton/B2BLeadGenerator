"""Backfill SIREN à partir d'un NOM + VILLE.

Pour les leads découverts sans SIREN (Instagram-first surtout). Via
recherche-entreprises.api.gouv.fr (sans clé), validé pour éviter le faux match :
  - NAF CHR (le résultat doit être un établissement CHR),
  - même département (si CP fourni),
  - concordance de nom (un token distinctif commun) — ici c'est fiable car
    l'API matche sur la vraie dénomination (pas d'homonyme géographique façon
    Places).
Renvoie {siren, naf, enseigne} ou None. Fail-soft.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Optional

import requests

from .naf_classifier import classify_naf

API_URL = "https://recherche-entreprises.api.gouv.fr/search"

# Mots génériques ignorés pour juger la concordance de nom.
_GENERIC = {
    "le", "la", "les", "du", "de", "des", "et", "aux", "au", "chez", "paris",
    "cafe", "bar", "restaurant", "brasserie", "hotel", "resto", "bistro",
    "bistrot", "traiteur", "pizzeria", "boulangerie", "snack", "food",
}


def _tokens(text: Optional[str]) -> set:
    text = (text or "").lower()
    text = "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")
    return {t for t in re.split(r"[^a-z0-9]+", text) if len(t) > 1 and t not in _GENERIC and not t.isdigit()}


def _best_match(results: List[Dict[str, Any]], name: str, dep: Optional[str]) -> Optional[Dict[str, Optional[str]]]:
    """Sélection PURE (testable) parmi des résultats recherche-entreprises."""
    want = _tokens(name)
    for res in results:
        siege = res.get("siege") or {}
        naf = siege.get("activite_principale") or res.get("activite_principale")
        cp = siege.get("code_postal") or ""
        enseigne = (siege.get("liste_enseignes") or [None])[0] or res.get("nom_complet")
        hay = _tokens(f"{enseigne} {res.get('nom_complet', '')}")
        name_ok = bool(want) and bool(want & hay)
        dep_ok = (not dep) or cp.startswith(dep)
        if naf and classify_naf(naf) and name_ok and dep_ok:
            return {"siren": res.get("siren"), "naf": naf, "enseigne": enseigne}
    return None


def backfill_siren(
    name: str, city: str, postal: Optional[str] = None, timeout: int = 10
) -> Optional[Dict[str, Optional[str]]]:
    """Nom + ville -> {siren, naf, enseigne} validé, ou None."""
    if not name:
        return None
    params: Dict[str, Any] = {"q": " ".join(filter(None, [name, city])), "per_page": 5}
    if postal:
        params["code_postal"] = postal
    try:
        resp = requests.get(API_URL, params=params, timeout=timeout)
        resp.raise_for_status()
        results = resp.json().get("results", [])
    except Exception:
        return None
    return _best_match(results, name, postal[:2] if postal else None)
