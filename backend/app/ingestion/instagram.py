"""Source Instagram-first via Apify (hashtag scraper) — [PHASE 2].

Apify renvoie des posts BRUTS (tous secteurs, toutes régions). On FILTRE pour ne
garder que le CHR en (pré-)ouverture en Île-de-France, on en tire
`{handle, nom, ville}`, puis (dans le pipeline) on backfill le SIREN et on
réutilise tout l'enrichissement existant.

Nécessite `APIFY_TOKEN` dans l'environnement (sinon no-op, fail-soft).
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from typing import Any, Dict, List, Optional

import requests

APIFY_ACTOR = "apify~instagram-hashtag-scraper"
DEFAULT_HASHTAGS = [
    "ouvertureprochaine", "nouvelleadresse", "nouveaurestaurant",
    "comingsoonparis", "prochainement",
]

# Mots-clés CHR (dans nom/caption/hashtags).
CHR_KEYWORDS = (
    "restaurant", "resto", "cafe", "coffee", "coffeeshop", "bar", "brasserie",
    "boulangerie", "patisserie", "traiteur", "bistrot", "bistro", "pizzeria",
    "cuisine", "salon de the", "glacier", "creperie", "cave a vin", "bar a vin",
    "gastronomie", "food", "snack", "burger", "sushi", "ramen", "tacos",
)
# Indices Île-de-France (villes fréquentes + Paris).
IDF_HINTS = (
    "paris", "nanterre", "boulogne", "saint-denis", "st-denis", "montreuil",
    "creteil", "versailles", "issy", "levallois", "neuilly", "vincennes",
    "montrouge", "clichy", "asnieres", "courbevoie", "puteaux", "ivry", "vitry",
    "aubervilliers", "pantin", "bagnolet", "malakoff", "vanves", "charenton",
    "colombes", "rueil", "suresnes", "meudon", "sceaux", "antony",
)
IDF_DEPTS = ("75", "77", "78", "91", "92", "93", "94", "95")


def has_token() -> bool:
    return bool(os.getenv("APIFY_TOKEN"))


def _norm(text: Optional[str]) -> str:
    text = (text or "").lower()
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


def _is_chr(text: str) -> bool:
    t = _norm(text)
    return any(kw in t for kw in CHR_KEYWORDS)


def _is_idf(text: str) -> bool:
    t = _norm(text)
    if any(h in t for h in IDF_HINTS):
        return True
    for m in re.findall(r"\b(\d{5})\b", t):
        if m[:2] in IDF_DEPTS:
            return True
    return False


def _post_text(post: Dict[str, Any]) -> str:
    return " ".join(filter(None, [
        post.get("ownerFullName"),
        post.get("caption"),
        " ".join(post.get("hashtags") or []),
        post.get("locationName"),
    ]))


def scrape_hashtags(
    hashtags: Optional[List[str]] = None, limit: int = 40, timeout: int = 300
) -> List[Dict[str, Any]]:
    """Appelle l'actor Apify. Renvoie les posts bruts (ou [] si pas de token/erreur)."""
    token = os.getenv("APIFY_TOKEN")
    if not token:
        return []
    url = f"https://api.apify.com/v2/acts/{APIFY_ACTOR}/run-sync-get-dataset-items?token={token}"
    body = {"hashtags": hashtags or DEFAULT_HASHTAGS, "resultsLimit": limit}
    try:
        resp = requests.post(url, json=body, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return []


def discover(posts: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Posts bruts -> [{handle, name, city}] : CHR + IdF, dédupliqués par handle.
    Fonction PURE (testable)."""
    seen: set = set()
    out: List[Dict[str, str]] = []
    for post in posts:
        handle = (post.get("ownerUsername") or "").strip()
        if not handle or handle in seen:
            continue
        text = _post_text(post)
        location = post.get("locationName") or ""
        if not _is_chr(text):
            continue
        if not _is_idf(f"{location} {post.get('caption', '')} {' '.join(post.get('hashtags') or [])}"):
            continue
        seen.add(handle)
        out.append({
            "handle": handle,
            "name": (post.get("ownerFullName") or handle).strip(),
            "city": _city_from_location(location),
            "type": _chr_type(text),  # pré-classé (validé CHR à la découverte)
            "caption": (post.get("caption") or "")[:300],  # pour le juge LLM
        })
    return out


def judge(candidates: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Juge LLM (le "flou") — ne garde que les VRAIS établissements CHR qui
    ouvrent/viennent d'ouvrir en Île-de-France, et nettoie le nom. Un filtre par
    mots-clés ne sait pas distinguer "marque qui utilise le hashtag" d'un "lieu
    qui ouvre" (cas yeat.fr) ; c'est le boulot du LLM.

    Fail-soft : sans OPENAI_API_KEY (ou en cas d'erreur) -> renvoie l'entrée
    telle quelle (on retombe sur le seul filtre heuristique)."""
    key = os.getenv("OPENAI_API_KEY")
    if not key or not candidates:
        return candidates
    try:
        from openai import OpenAI
    except ImportError:
        return candidates

    listing = "\n".join(
        f'{i}. @{c["handle"]} | nom: {c["name"]} | lieu: {c.get("city")} '
        f'| légende: {c.get("caption", "")}'
        for i, c in enumerate(candidates)
    )
    system = (
        "Tu qualifies des comptes Instagram pour un fournisseur B2B de luminaires/"
        "mobilier ciblant le CHR (café, restaurant, bar, hôtel, brasserie, "
        "boulangerie, traiteur, salon de thé). Tu ne gardes QUE les comptes d'un "
        "VRAI établissement/lieu physique qui OUVRE bientôt ou vient d'ouvrir en "
        "Île-de-France. Tu JETTES : marques/produits sans lieu, autres secteurs "
        "(sport, beauté, bijoux…), établissements déjà bien établis, autres régions. "
        "Réponds STRICTEMENT en JSON."
    )
    user = (
        f"Voici {len(candidates)} comptes. Pour chacun, décide keep (true/false) et "
        "donne un nom d'enseigne propre (sans emojis ni slogan).\n"
        'Format EXACT : {"results":[{"index":0,"keep":true,"name":"Enseigne"}]}\n\n'
        f"{listing}"
    )
    try:
        client = OpenAI(api_key=key)
        completion = client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            response_format={"type": "json_object"},
            temperature=0,
        )
        data = json.loads(completion.choices[0].message.content)
        by_index = {int(r["index"]): r for r in data.get("results", []) if "index" in r}
    except Exception:
        return candidates

    kept: List[Dict[str, str]] = []
    for i, c in enumerate(candidates):
        r = by_index.get(i)
        if r and r.get("keep"):
            c2 = dict(c)
            if r.get("name"):
                c2["name"] = str(r["name"]).strip()
            kept.append(c2)
    return kept


def _chr_type(text: str) -> str:
    """Sous-type CHR à partir des mots-clés (le lead est déjà validé CHR)."""
    t = _norm(text)
    if "hotel" in t:
        return "hôtel"
    if "coffeeshop" in t or "coffee shop" in t:
        return "coffee shop"
    if any(k in t for k in ("cafe", "coffee", "salon de the", "boulangerie", "patisserie", "glacier")):
        return "café"
    if any(k in t for k in ("bar", "brasserie", "cave a vin", "bar a vin")):
        return "bar"
    if "traiteur" in t:
        return "traiteur"
    return "restaurant"


def _city_from_location(location: str) -> str:
    """Extrait une ville exploitable de locationName (ex: 'Nanterre Prefecture'
    -> 'Nanterre'). Défaut : 'Paris'."""
    loc = (location or "").strip()
    if not loc:
        return "Paris"
    # Premier segment avant une virgule / mot parasite.
    first = re.split(r"[,\-]", loc)[0].strip()
    return first or "Paris"
