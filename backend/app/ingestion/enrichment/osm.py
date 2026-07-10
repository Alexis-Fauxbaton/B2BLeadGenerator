"""Lookup OpenStreetMap (Overpass) — contacts depuis les tags des établissements.

Gratuit, sans clé. À partir de la géoloc d'un lead, on cherche le nœud CHR le
plus proche dont le nom correspond, et on lit ses tags de contact.
Données sous licence ODbL (stockables).
"""
from __future__ import annotations

import re
import time
import unicodedata
from typing import Any, Dict, List, Optional

import requests

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
HEADERS = {"User-Agent": "CHR-Signal-Radar/0.1 (lead enrichment)"}


def _normalize(text: str) -> str:
    text = (text or "").lower()
    text = "".join(
        c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"[^a-z0-9 ]", " ", text)


def _tokens(text: str) -> set:
    stop = {"le", "la", "les", "de", "du", "des", "chez", "restaurant", "cafe", "bar", "sas", "sarl"}
    return {t for t in _normalize(text).split() if len(t) > 2 and t not in stop}


def _name_matches(query_name: str, osm_name: str) -> bool:
    a, b = _tokens(query_name), _tokens(osm_name)
    if not a or not b:
        return False
    # Au moins un token significatif commun.
    return bool(a & b)


def _extract_instagram(value: str) -> Optional[str]:
    if not value:
        return None
    m = re.search(r"instagram\.com/([A-Za-z0-9_.]+)", value)
    if m:
        return m.group(1)
    return value.lstrip("@") or None


def lookup_osm(
    name: str,
    lat: float,
    lon: float,
    radius: int = 150,
    timeout: int = 25,
) -> Dict[str, Optional[str]]:
    """Renvoie les contacts trouvés dans OSM autour de (lat, lon) pour `name`.

    Clés : phone, website, instagram, email, facebook. Valeurs None si absentes.
    Tolérant aux pannes : renvoie un dict vide-ish en cas d'erreur.
    """
    empty = {"phone": None, "website": None, "instagram": None, "email": None,
             "facebook": None, "name": None}
    if lat is None or lon is None:
        return empty

    query = f"""
[out:json][timeout:{timeout}];
(
  node(around:{radius},{lat},{lon})["amenity"~"restaurant|cafe|bar|fast_food|pub"];
  node(around:{radius},{lat},{lon})["tourism"="hotel"];
  way(around:{radius},{lat},{lon})["amenity"~"restaurant|cafe|bar|fast_food|pub"];
);
out tags 60;
"""
    try:
        resp = requests.post(
            OVERPASS_URL, data={"data": query}, headers=HEADERS, timeout=timeout + 5
        )
        resp.raise_for_status()
        elements: List[Dict[str, Any]] = resp.json().get("elements", [])
    except Exception:
        return empty

    # Choisit le meilleur élément dont le nom correspond.
    best: Optional[Dict[str, Any]] = None
    for el in elements:
        tags = el.get("tags", {})
        if _name_matches(name, tags.get("name", "")):
            best = tags
            break
    if best is None:
        return empty

    return {
        "phone": best.get("phone") or best.get("contact:phone"),
        "website": best.get("website") or best.get("contact:website"),
        "instagram": _extract_instagram(best.get("contact:instagram", "")),
        "email": best.get("email") or best.get("contact:email"),
        "facebook": best.get("contact:facebook"),
        "name": best.get("name"),  # -> verrou d'identité côté enricher
    }
