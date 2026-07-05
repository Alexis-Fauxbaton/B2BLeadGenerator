"""Matching lead Insta -> SIREN/SIRET via le registre (brique 1 du pivot).

Remplace `backfill_siren`. Chaîne : nom nettoyé (auto-accept seulement si
cohérence géo) -> adresse (BAN -> /near_point) -> arbitre LLM sur candidats
ambigus. JAMAIS de merge nom-seul sans géo ni arbitre (piège Auréa : un
"AUREA" 56.10A existe à Théoule, la bio "bijoux, Portugal" doit le rejeter).
Fail-soft partout. Cf. docs/inventory-pivot-design.md.
"""
from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Dict, List, Optional, Tuple

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
            "date_debut_activite": etab.get("date_debut_activite"),
        })
    return out


def _age_label(date_str: Optional[str], today: Optional[date] = None) -> str:
    """'2025-07-04' -> 'il y a 12 mois' (arithmétique faite en CODE : les
    petits LLM ratent les comparaisons de dates brutes — vécu rounds 1-3)."""
    if not date_str:
        return "?"
    try:
        d = date.fromisoformat(str(date_str)[:10])
    except ValueError:
        return "?"
    today = today or date.today()
    if d > today:
        return "dans le futur"
    months = (today.year - d.year) * 12 + (today.month - d.month)
    if months == 0:
        return "ce mois-ci"
    if months < 24:
        return f"il y a {months} mois"
    return f"il y a {months // 12} ans"


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


_BAN_MIN_SCORE = 0.6  # en dessous, le géocodage pointe souvent la mauvaise rue


def geocode(address: Optional[str], fetch: Fetch) -> Optional[Tuple[float, float]]:
    """Adresse libre -> (lat, lon) via BAN, None si introuvable ou score faible."""
    if not address:
        return None
    data = fetch(BAN_URL, {"q": address, "limit": 1})
    feats = data.get("features") or []
    if not feats:
        return None
    props = feats[0].get("properties") or {}
    if (props.get("score") or 0) < _BAN_MIN_SCORE:
        return None
    lon, lat = feats[0]["geometry"]["coordinates"]
    return (lat, lon)


def near_candidates(lat: float, lon: float, fetch: Fetch,
                    radius: float = 0.1) -> List[Dict[str, Any]]:
    """Établissements hébergement-restauration (section I) autour d'un point."""
    data = fetch(NEAR_URL, {"lat": lat, "long": lon, "radius": radius,
                            "section_activite_principale": "I", "per_page": 10})
    return _candidates(data.get("results") or [])


def pick_by_address(cands: List[Dict[str, Any]], num: Optional[str],
                    name: str) -> Tuple[str, List[Dict[str, Any]]]:
    """Sélection PURE par adresse. Candidats CHR au MÊME numéro de voie :
    1 -> match ; plusieurs -> ambigu (arbitre) ; 0 -> none. L'overlap de nom
    court-circuite l'ambiguïté (ex. enseigne identique au bon numéro)."""
    if not num:
        return ("none", [])
    same = [c for c in cands
            if c["naf"] and classify_naf(c["naf"]) and street_number(c["adresse"]) == num]
    if not same:
        return ("none", [])
    named = [c for c in same if _name_overlap(name, f'{c["nom"]} {c["enseignes"]}')]
    if len(named) == 1:
        return ("match", named)
    if len(same) == 1:
        return ("match", same)
    return ("ambiguous", same)


_ARBITER_SYSTEM = (
    "Tu relies un compte Instagram d'établissement CHR à son entreprise au "
    "registre Sirene. On te donne le nom Insta, un extrait de bio, et des "
    "candidats du registre (nom légal, enseignes, NAF, adresse, date de "
    "création). Le nom légal peut être SANS RAPPORT avec le nom commercial "
    "(holding, patronyme) : juge sur le faisceau adresse/NAF/récence/enseigne. "
    "Si plusieurs candidats occupent la MÊME adresse (succession d'exploitants), "
    "privilégie celui dont la date de création est cohérente avec les signaux "
    "du compte (compte annonçant une ouverture récente/à venir -> candidat le "
    "plus récent). "
    "Si le compte n'est manifestement PAS un établissement CHR français "
    "(marque, hors France, média), ou si aucun candidat ne colle : null. "
    "Raisonne d'abord brièvement (cohérence géo, temporelle, enseigne) puis "
    'décide. Réponds STRICTEMENT en JSON : {"reasoning": "<2 phrases max>", '
    '"match_index": <int|null>}.'
)


def _openai_client():
    """Client OpenAI ou None (fail-soft), même convention qu'instagram.py."""
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        return None
    try:
        from openai import OpenAI
        return OpenAI(api_key=key)
    except Exception:
        return None


def arbitrate(name: str, context: Optional[str],
              cands: List[Dict[str, Any]], client=None) -> Optional[str]:
    """Arbitre LLM : SIREN du candidat retenu, ou None (rejet / fail-soft)."""
    if client is None or not cands:
        return None
    listing = "\n".join(
        f'{i}. {c["nom"]} | enseignes: {c["enseignes"] or "-"} | NAF {c["naf"]} '
        f'| {c["adresse"]} | société créée {_age_label(c["date_creation"])}'
        + (f' | activité démarrée {_age_label(c.get("date_debut_activite"))}'
           if c.get("date_debut_activite") else "")
        for i, c in enumerate(cands)
    )
    user = (f"Date du jour : {date.today().isoformat()}\n"
            f"Compte Insta : {name}\nBio/contexte : {(context or '')[:600]}\n\n"
            f"Candidats registre :\n{listing}\n\n"
            f'Format EXACT : {{"reasoning": "...", "match_index": <int|null>}}')
    try:
        completion = client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            messages=[{"role": "system", "content": _ARBITER_SYSTEM},
                      {"role": "user", "content": user}],
            response_format={"type": "json_object"},
            temperature=0,
        )
        idx = json.loads(completion.choices[0].message.content).get("match_index")
        if isinstance(idx, int) and 0 <= idx < len(cands):
            return cands[idx]["siren"]
    except Exception:
        pass
    return None


@dataclass
class MatchResult:
    siren: Optional[str]
    siret: Optional[str]
    naf: Optional[str]
    enseigne: Optional[str]
    confidence: str  # "haute" | "moyenne"
    method: str      # "nom" | "adresse" | "arbitre"


# Sentinel : "résous le client OpenAI depuis l'env". Passer None = SANS arbitre
# (déterministe, aucun appel LLM — indispensable pour les tests).
_USE_ENV = object()


def _result(cand: Dict[str, Any], confidence: str, method: str) -> MatchResult:
    enseigne = cand["enseignes"] or cand["nom"] or None
    return MatchResult(siren=cand["siren"], siret=cand["siret"], naf=cand["naf"],
                       enseigne=enseigne, confidence=confidence, method=method)


def match(name: str, city: Optional[str] = None, postal: Optional[str] = None,
          address: Optional[str] = None, context: Optional[str] = None,
          fetch: Fetch = _http_get, llm_client=_USE_ENV) -> Optional[MatchResult]:
    """Chaîne complète nom -> adresse -> arbitre. Chaque étage ne traite que ce
    que le précédent n'a pas résolu. None = pas de merge (le lead vit sans
    SIREN, la réconciliation retentera)."""
    if not name and not address:
        return None

    pool: List[Dict[str, Any]] = []  # candidats ambigus pour l'arbitre

    # 1. Nom (auto-accept seulement si géo cohérente).
    name_cands = search_by_name(name, city, postal, fetch) if name else []
    got = pick_by_name(name_cands, name, city, postal)
    if got:
        return _result(got, "haute", "nom")
    pool += [c for c in name_cands
             if c["naf"] and classify_naf(c["naf"])
             and _name_overlap(name, f'{c["nom"]} {c["enseignes"]}')]

    # 2. Adresse (candidat CHR unique au même numéro = quasi décisif).
    if address:
        coords = geocode(address, fetch)
        if coords:
            near = near_candidates(coords[0], coords[1], fetch)
            verdict, chosen = pick_by_address(near, street_number(address), name)
            if verdict == "match":
                return _result(chosen[0], "moyenne", "adresse")
            if verdict == "ambiguous":
                pool += chosen

    # 3. Arbitre LLM sur le pool résiduel (dédupliqué par SIREN).
    if pool:
        uniq = list({c["siren"]: c for c in pool}.values())
        client = _openai_client() if llm_client is _USE_ENV else llm_client
        siren = arbitrate(name, context, uniq, client)
        if siren:
            cand = next(c for c in uniq if c["siren"] == siren)
            return _result(cand, "moyenne", "arbitre")
    return None
