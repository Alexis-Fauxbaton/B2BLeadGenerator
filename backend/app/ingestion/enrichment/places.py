"""Connecteur Google Places (New) — téléphone / site / statut.

Optionnel : activé seulement si GOOGLE_PLACES_API_KEY est défini.
Bonne couverture des lieux physiques (même récents). ATTENTION : la recherche
texte peut renvoyer un mauvais établissement -> on VALIDE le match (type CHR +
localisation) avant d'accepter, sinon on rejette (mieux vaut rien qu'un faux).

Note ToS : Google limite le stockage durable des données Places. Pour un PoC on
les met en base ; en production il faudrait re-fetch à l'affichage (ne garder que
le place_id). Voir docs/contact-enrichment-design.md.
"""
from __future__ import annotations

import math
import os
import time
import unicodedata
from typing import Callable, Dict, List, Optional, Tuple

import requests

SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"

FIELD_MASK = ",".join([
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.location",  # lat/lon du résultat -> validation par distance
    "places.nationalPhoneNumber",
    "places.websiteUri",
    "places.businessStatus",
    "places.primaryType",
    "places.userRatingCount",  # nb d'avis -> proxy de fraîcheur / d'ancienneté
])

# Rayon (m) sous lequel la proximité au point Sirene CONFIRME le match (signal
# fort). Au-delà, on ne rejette PAS : le siège social est très souvent à
# plusieurs km du local réel (mesuré : 0,9 à 6 km), donc une distance élevée
# n'est pas une preuve de mauvais match -> on laisse le texte décider.
MAX_MATCH_DISTANCE_M = 200.0

# Types Places considérés comme CHR (validation du match).
CHR_PLACE_TYPES = {
    "cafe", "coffee_shop", "bar", "pub", "wine_bar", "bistro", "hotel", "lodging",
    "bakery", "meal_takeaway", "meal_delivery", "fast_food_restaurant",
    "resort_hotel", "bed_and_breakfast", "food", "brewery", "tea_house",
}


def has_key() -> bool:
    return bool(os.getenv("GOOGLE_PLACES_API_KEY"))


def _normalize(text: str) -> str:
    text = (text or "").lower()
    return "".join(
        c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn"
    )


def _is_chr_type(primary_type: str) -> bool:
    pt = (primary_type or "").lower()
    return "restaurant" in pt or pt in CHR_PLACE_TYPES


def _location_ok(formatted_address: str, postal: Optional[str], city: Optional[str]) -> bool:
    # Repli texte (quand on n'a pas de coordonnées d'ancrage Sirene).
    # Postal OU ville : le siège social (Sirene) est souvent dans un autre
    # arrondissement que le lieu réel -> exiger le postal exact rejette des bons
    # matchs. La garde forte contre le mauvais établissement reste le type CHR.
    addr = _normalize(formatted_address)
    if postal and postal in addr:
        return True
    if city and _normalize(city) in addr:
        return True
    return False


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance en mètres entre deux points (lat/lon en degrés)."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _location_ok_geo(
    place_lat: Optional[float], place_lon: Optional[float],
    anchor_lat: Optional[float], anchor_lon: Optional[float],
) -> Optional[bool]:
    """Validation forte par distance au point Sirene. Renvoie True/False si on
    peut décider, None si les coordonnées manquent (-> repli texte)."""
    if None in (place_lat, place_lon, anchor_lat, anchor_lon):
        return None
    return _haversine_m(place_lat, place_lon, anchor_lat, anchor_lon) <= MAX_MATCH_DISTANCE_M


def _match_ok(
    primary_type: str,
    formatted_address: str,
    postal: Optional[str],
    city: Optional[str],
    place_lat: Optional[float] = None,
    place_lon: Optional[float] = None,
    anchor_lat: Optional[float] = None,
    anchor_lon: Optional[float] = None,
) -> bool:
    """Décision de match complète (fonction pure, testable sans réseau).

    Gate dur : type CHR. Pour la localisation, deux chemins d'ACCEPTATION (la
    distance confirme mais n'oppose jamais de veto, car le siège Sirene est
    souvent loin du local) :
      - proximité au point Sirene (<= rayon) -> confirmé fort, accepté ;
      - sinon, repli texte (CP OU ville présent dans l'adresse Places).
    Une distance élevée ne rejette donc pas : elle renvoie au texte."""
    return _match_basis(
        primary_type, formatted_address, postal, city,
        place_lat, place_lon, anchor_lat, anchor_lon,
    ) is not None


def _match_basis(
    primary_type: str,
    formatted_address: str,
    postal: Optional[str],
    city: Optional[str],
    place_lat: Optional[float] = None,
    place_lon: Optional[float] = None,
    anchor_lat: Optional[float] = None,
    anchor_lon: Optional[float] = None,
) -> Optional[str]:
    """Comme _match_ok mais renvoie la BASE du match (pilote la confiance) :
    'geo' (proximité Sirene confirmée) | 'text' (nom+ville) | None (rejeté)."""
    if not _is_chr_type(primary_type):
        return None
    if _location_ok_geo(place_lat, place_lon, anchor_lat, anchor_lon) is True:
        return "geo"
    if _location_ok(formatted_address, postal, city):
        return "text"
    return None


def lookup_places(
    name: str,
    city: Optional[str] = None,
    postal: Optional[str] = None,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    timeout: int = 15,
) -> Dict[str, Optional[object]]:
    """Cherche l'établissement sur Places et renvoie ses contacts SI le match est
    validé (type CHR + localisation). Sinon dict vide (matched=False).

    Si lat/lon (ancrage Sirene) sont fournis, la localisation est validée par
    DISTANCE au lieu retourné (verrou fort, lève l'ambiguïté des homonymes
    parisiens) ; sinon on retombe sur le repli texte (postal OU ville)."""
    empty = {
        "phone": None, "website": None, "business_status": None,
        "review_count": None, "place_id": None, "matched": False, "match_basis": None,
        "display_name": None,
    }
    key = os.getenv("GOOGLE_PLACES_API_KEY")
    if not key or not name:
        return empty

    query = " ".join(filter(None, [name, city]))
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": key,
        "X-Goog-FieldMask": FIELD_MASK,
    }
    try:
        resp = requests.post(
            SEARCH_URL,
            headers=headers,
            json={"textQuery": query, "regionCode": "FR", "maxResultCount": 1},
            timeout=timeout,
        )
        resp.raise_for_status()
        places = resp.json().get("places", [])
    except Exception:
        return empty

    if not places:
        return empty
    p = places[0]

    primary_type = p.get("primaryType", "")
    formatted = p.get("formattedAddress", "")
    loc = p.get("location") or {}

    # Validation : type CHR ET localisation cohérente (distance Sirene > texte).
    basis = _match_basis(
        primary_type, formatted, postal, city,
        loc.get("latitude"), loc.get("longitude"), lat, lon,
    )
    if basis is None:
        return {**empty, "place_id": p.get("id")}  # vu mais rejeté

    return {
        "phone": p.get("nationalPhoneNumber"),
        "website": p.get("websiteUri"),
        "business_status": p.get("businessStatus"),
        "review_count": p.get("userRatingCount", 0),
        "place_id": p.get("id"),
        "matched": True,
        "match_basis": basis,
        "display_name": (p.get("displayName") or {}).get("text"),
    }


# --- Balayage volume (B2, T3) -------------------------------------------
#
# `search_places_text` est un AJOUT PUR : il ne touche ni `lookup_places`, ni
# `_match_ok`/`_match_basis`, ni `CHR_PLACE_TYPES` (le gate CHR reste
# exclusivement sur le chemin d'enrichissement `contact_enricher`). Destiné
# au balayage archi (`places_sweep.PlacesArchiConnector`) : field mask
# Contact UNIQUEMENT (pas d'Atmosphere/reviews, décision #7) et
# `maxResultCount` jusqu'à 20 -> jusqu'à 20 fiches pleinement enrichies par
# appel FACTURÉ (SKU Text Search Enterprise), contre 1 pour `lookup_places`.

ARCHI_FIELD_MASK = ",".join([
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.location",
    "places.nationalPhoneNumber",
    "places.websiteUri",
    "places.userRatingCount",
    "places.primaryType",
])

# Throttle "poli" entre appels du balayage (Google n'impose rien -> le
# budget € dur reste le régulateur principal, cf. places_sweep.py).
_ARCHI_THROTTLE_S = 0.2


def _archi_post(url: str, headers: Dict[str, str], json: Dict[str, object],
                 timeout: int = 15) -> Dict[str, object]:
    """Poster réseau par défaut (throttlé), injecté en test via `api_post`."""
    time.sleep(_ARCHI_THROTTLE_S)
    resp = requests.post(url, headers=headers, json=json, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def search_places_text(
    query: str,
    api_post: Optional[Callable[..., Dict[str, object]]] = None,
    page_token: Optional[str] = None,
    max_results: int = 20,
) -> Tuple[List[Dict[str, object]], Optional[str], bool]:
    """Text Search (New), field mask Contact SEULEMENT, `maxResultCount`
    jusqu'à 20 (décision #7). AUCUN gate CHR (contrairement à
    `lookup_places` -- `_is_chr_type` rejetterait tout studio d'archi) :
    c'est à l'appelant (`places_sweep._archi_ok`) de filtrer.

    Renvoie `(places, next_page_token, billed)`. `billed` est True SSI un
    appel réseau a réellement été tenté ET a abouti (réponse reçue de
    `poster`) -- c'est le SEUL signal fiable pour décider si l'appel doit
    compter dans un budget € dur (`places_sweep.PlacesArchiConnector`) :
    sans lui, une clé manquante/vide ou une exception réseau avalée
    ressemble en surface à une recherche réussie sans résultat (`[], None`),
    ce qui épuiserait le budget mensuel pour zéro coût Google réel.
    `billed=False` dans les deux cas fail-soft :
    - pas de clé Google / requête vide -> `([], None, False)` (aucun appel
      réseau tenté) ;
    - exception levée par `poster` (réseau, timeout, HTTP...) -> `([],
      None, False)` (appel tenté mais pas confirmé abouti -- on ne facture
      jamais un appel dont on ignore s'il a réellement été traité par
      Google).
    `api_post=None` -> poster réseau par défaut (throttlé) ; injecté en
    test (aucun réseau)."""
    key = os.getenv("GOOGLE_PLACES_API_KEY")
    if not key or not query:
        return [], None, False

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": key,
        "X-Goog-FieldMask": ARCHI_FIELD_MASK,
    }
    body: Dict[str, object] = {
        "textQuery": query,
        "regionCode": "FR",
        "maxResultCount": max_results,
    }
    if page_token:
        body["pageToken"] = page_token

    poster = api_post or _archi_post
    try:
        data = poster(SEARCH_URL, headers=headers, json=body) or {}
    except Exception:
        return [], None, False

    places: List[Dict[str, object]] = []
    for p in data.get("places", []) or []:
        places.append({
            "id": p.get("id"),
            "name": (p.get("displayName") or {}).get("text"),
            "address": p.get("formattedAddress"),
            "phone": p.get("nationalPhoneNumber"),
            "website": p.get("websiteUri"),
            "rating_count": p.get("userRatingCount"),
            "primary_type": p.get("primaryType"),
        })
    return places, data.get("nextPageToken"), True
