# SIRET Matcher (brique 1) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Un module `siret_matcher.py` qui relie un lead Instagram (nom, ville, adresse, bio) à son SIREN/SIRET via le registre, remplaçant `backfill_siren`, avec éval de non-régression sur les 20 snapshots.

**Architecture:** Chaîne à 3 étages — nom nettoyé (auto-accept seulement si cohérence géo), adresse (BAN → `/near_point` NAF section I, auto-accept si candidat CHR unique au même numéro), arbitre LLM sur les candidats ambigus (jamais de merge nom-seul sans géo ni arbitre — piège Auréa). Transport HTTP injectable pour tests/fixtures. Fail-soft partout.

**Tech Stack:** Python 3.9 (pas de `X | None`), requests, OpenAI (optionnel, fail-soft), pytest. API : recherche-entreprises.api.gouv.fr (`/search`, `/near_point`), api-adresse.data.gouv.fr (BAN). Toutes sans clé.

## Global Constraints

- Python 3.9 : `Optional[X]`/`Dict`/`List` de `typing`, jamais `X | None`.
- Fail-soft : aucune erreur réseau/LLM ne remonte ; retour `None`/`{}` (convention des enrichisseurs du repo).
- Throttle : ≥ 0,15 s entre appels recherche-entreprises (limite 7 req/s).
- Docstrings/commentaires en français (convention du repo).
- Répertoire de travail : commandes `python`/`pytest` depuis `chr-signal-radar/backend` ; commandes `git` depuis la racine `chr-signal-radar/` (les chemins `backend/...` des commits sont relatifs à cette racine).
- Tests : `python -m pytest tests/ -q` doit passer à la fin de CHAQUE tâche.
- **Jamais de merge nom-seul** sans cohérence géo ou verdict d'arbitre.

---

### Task 1: Helpers purs (`clean_name`, `street_number`, tokens)

**Files:**
- Create: `backend/app/ingestion/enrichment/siret_matcher.py`
- Create: `backend/tests/test_siret_matcher.py`

**Interfaces:**
- Produces: `clean_name(raw: Optional[str]) -> str`, `street_number(address: Optional[str]) -> Optional[str]`, `_tokens(text: Optional[str]) -> set`, `_name_overlap(ig_name: str, sirene_text: str) -> bool`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_siret_matcher.py
"""Tests du matcher Insta -> SIREN/SIRET (cas réels des snapshots d'éval)."""
from app.ingestion.enrichment.siret_matcher import (
    clean_name,
    street_number,
    _name_overlap,
)


def test_clean_name_strips_emojis_and_decorations():
    assert clean_name("MOKA ☕️ Coffee shop & Matcha Bar 🍵") == "MOKA Coffee shop & Matcha Bar"
    # 𝐺𝑖𝑜𝑟𝑔𝑖𝑛𝑎 en "mathematical alphanumeric symbols" -> NFKC -> Giorgina
    assert clean_name("\U0001d43a\U0001d456\U0001d45c\U0001d45f\U0001d454\U0001d456\U0001d45b\U0001d44e 💙") == "Giorgina"


def test_clean_name_keeps_first_segment_before_separators():
    assert clean_name("LE MOURE ROUGE - CANNES 🛟") == "LE MOURE ROUGE"
    assert clean_name("VILLA HENRIETTE • CABOURG") == "VILLA HENRIETTE"
    assert clean_name("Brasserie de la Fontaine • Lourmarin") == "Brasserie de la Fontaine"
    assert clean_name("l'Artémise-Salon de thé") == "l'Artémise"


def test_clean_name_handles_empty():
    assert clean_name(None) == ""
    assert clean_name("🍕🍕") == ""


def test_street_number():
    assert street_number("143  Av. du Général de Gaule Sartrouville") == "143"
    assert street_number("11 rue du Colisée, 75008, Paris") == "11"
    assert street_number("Place de la Fontaine, Lourmarin") is None
    assert street_number(None) is None


def test_name_overlap_uses_distinctive_tokens():
    # 'restaurant'/'le'/'la' sont génériques : pas de match dessus.
    assert _name_overlap("Tre Gusto", "SAR FOOD") is False
    assert _name_overlap("LE MOURE ROUGE", "LE MOURE ROUGE 56.10A CANNES") is True
    assert _name_overlap("LE MOURE ROUGE", "COMMUNE DE CANNES MAIRIE") is False
    assert _name_overlap("CHÈRES COUSINES", "CC ROQUETTE (CHERES COUSINES)") is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_siret_matcher.py -q`
Expected: FAIL — `ModuleNotFoundError` ou `ImportError` (module absent).

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/ingestion/enrichment/siret_matcher.py
"""Matching lead Insta -> SIREN/SIRET via le registre (brique 1 du pivot).

Remplace `backfill_siren`. Chaîne : nom nettoyé (auto-accept seulement si
cohérence géo) -> adresse (BAN -> /near_point) -> arbitre LLM sur candidats
ambigus. JAMAIS de merge nom-seul sans géo ni arbitre (piège Auréa : un
"AUREA" 56.10A existe à Théoule, la bio "bijoux, Portugal" doit le rejeter).
Fail-soft partout. Cf. docs/inventory-pivot-design.md.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Optional

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_siret_matcher.py -q`
Expected: PASS (5 tests). Si `clean_name` échoue sur l'apostrophe de "l'Artémise" (catégorie Po, conservée) ou le tiret non espacé, ajuster `_SEP_RE` — le comportement attendu est celui des asserts.

- [ ] **Step 5: Commit**

```bash
git add backend/app/ingestion/enrichment/siret_matcher.py backend/tests/test_siret_matcher.py
git commit -m "feat(matcher): helpers purs clean_name/street_number/overlap"
```

---

### Task 2: Normalisation des candidats + transport HTTP + recherche par nom

**Files:**
- Modify: `backend/app/ingestion/enrichment/siret_matcher.py`
- Test: `backend/tests/test_siret_matcher.py`

**Interfaces:**
- Consumes: `_name_overlap`, `_tokens` (Task 1)
- Produces: `Fetch = Callable[[str, Dict[str, Any]], Dict[str, Any]]` ; `_http_get(url, params) -> dict` (throttlé, fail-soft `{}`) ; `_candidates(results: List[dict]) -> List[dict]` — normalise en `{siren, siret, nom, enseignes, naf, adresse, cp, date_creation}` ; `search_by_name(name, city, postal, fetch) -> List[dict]` ; `pick_by_name(cands, name, city, postal) -> Optional[dict]` (pure — auto-accept seulement si géo cohérente)

- [ ] **Step 1: Write the failing tests**

Ajouter à `backend/tests/test_siret_matcher.py` :

```python
from app.ingestion.enrichment.siret_matcher import _candidates, pick_by_name

# Extraits réels de l'API recherche-entreprises (test du 2026-07-04).
HIT_MOURE = {
    "siren": "899355770", "nom_complet": "LE MOURE ROUGE",
    "activite_principale": "56.10A", "date_creation": "2021-05-17",
    "siege": {"siret": "89935577000012", "activite_principale": "56.10A",
              "adresse": "62 BOULEVARD DE LA CROISETTE 06400 CANNES",
              "code_postal": "06400", "liste_enseignes": None},
}
HIT_MAIRIE = {
    "siren": "210600292", "nom_complet": "COMMUNE DE CANNES",
    "activite_principale": "84.11Z", "date_creation": "1901-01-01",
    "siege": {"siret": "21060029200010", "activite_principale": "84.11Z",
              "adresse": "PL DE L HOTEL DE VILLE 06150 CANNES",
              "code_postal": "06150", "liste_enseignes": ["MAIRIE"]},
}
HIT_AUREA = {
    "siren": "105726145", "nom_complet": "AUREA",
    "activite_principale": "56.10A", "date_creation": "2026-05-28",
    "siege": {"siret": "10572614500014", "activite_principale": "56.10A",
              "adresse": "8 RUE DU LANGUEDOC 06590 THEOULE-SUR-MER",
              "code_postal": "06590", "liste_enseignes": None},
}
# Variante near_point : l'établissement matché est dans matching_etablissements.
HIT_OCOIN = {
    "siren": "989119201", "nom_complet": "OCOIN",
    "date_creation": "2025-01-15",
    "matching_etablissements": [{
        "siret": "98911920100011", "activite_principale": "56.10C",
        "adresse": "143 AVENUE GENERAL DE GAULLE 78500 SARTROUVILLE",
        "code_postal": "78500", "liste_enseignes": None,
        "date_creation": "2025-07-04",
    }],
}


def test_candidates_normalizes_siege_and_matching_etablissements():
    cands = _candidates([HIT_MOURE, HIT_OCOIN])
    assert cands[0]["siren"] == "899355770"
    assert cands[0]["naf"] == "56.10A"
    assert cands[0]["adresse"] == "62 BOULEVARD DE LA CROISETTE 06400 CANNES"
    # near_point : l'étage établissement prime sur le siège.
    assert cands[1]["siret"] == "98911920100011"
    assert cands[1]["naf"] == "56.10C"


def test_pick_by_name_accepts_with_geo_consistency():
    cands = _candidates([HIT_MAIRIE, HIT_MOURE])
    got = pick_by_name(cands, "LE MOURE ROUGE", city="Cannes", postal=None)
    # La mairie (NAF non-CHR, pas d'overlap distinctif) est ignorée.
    assert got is not None and got["siren"] == "899355770"


def test_pick_by_name_refuses_without_geo():
    # Piège Auréa : nom+NAF collent mais aucune géo connue -> PAS d'auto-accept
    # (ira à l'arbitre). Le backfill actuel aurait mergé à tort.
    cands = _candidates([HIT_AUREA])
    assert pick_by_name(cands, "AURÉA", city=None, postal=None) is None


def test_pick_by_name_refuses_geo_mismatch():
    cands = _candidates([HIT_AUREA])
    assert pick_by_name(cands, "AURÉA", city="Lisbonne", postal=None) is None


def test_http_get_fails_soft(monkeypatch):
    import app.ingestion.enrichment.siret_matcher as sm

    def boom(*a, **k):
        raise OSError("réseau HS")

    monkeypatch.setattr(sm.requests, "get", boom)
    assert sm._http_get(sm.SEARCH_URL, {"q": "x"}) == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_siret_matcher.py -q`
Expected: FAIL — `ImportError: cannot import name '_candidates'`.

- [ ] **Step 3: Write the implementation**

Ajouter à `siret_matcher.py` (imports en tête : `import time`, `from typing import Any, Callable, Dict, List`, `import requests`, `from .naf_classifier import classify_naf`) :

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_siret_matcher.py -q`
Expected: PASS (10 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/ingestion/enrichment/siret_matcher.py backend/tests/test_siret_matcher.py
git commit -m "feat(matcher): recherche par nom avec garde-fou geo (jamais nom-seul)"
```

---

### Task 3: Chemin adresse (BAN → `/near_point` → sélection)

**Files:**
- Modify: `backend/app/ingestion/enrichment/siret_matcher.py`
- Test: `backend/tests/test_siret_matcher.py`

**Interfaces:**
- Consumes: `_candidates`, `_name_overlap`, `street_number`, `Fetch` (Tasks 1-2)
- Produces: `geocode(address, fetch) -> Optional[Tuple[float, float]]` (None si score BAN < 0.6) ; `near_candidates(lat, lon, fetch, radius=0.1) -> List[dict]` ; `pick_by_address(cands, num, name) -> Tuple[str, List[dict]]` — pure, renvoie `("match", [cand])`, `("ambiguous", cands_chr)` ou `("none", [])`

- [ ] **Step 1: Write the failing tests**

Ajouter à `backend/tests/test_siret_matcher.py` :

```python
from app.ingestion.enrichment.siret_matcher import (
    geocode,
    near_candidates,
    pick_by_address,
)

HIT_SARFOOD = {
    "siren": "948225982", "nom_complet": "SAR FOOD",
    "matching_etablissements": [{
        "siret": "94822598200014", "activite_principale": "56.10C",
        "adresse": "143 AVENUE GENERAL DE GAULLE 78500 SARTROUVILLE",
        "code_postal": "78500", "liste_enseignes": None,
        "date_creation": "2023-03-24",
    }],
}
HIT_CAFETERIA = {
    "siren": "427984489", "nom_complet": "ASS CAFETERIA DES PTT",
    "matching_etablissements": [{
        "siret": "42798448900011", "activite_principale": "56.10A",
        "adresse": "145 AVENUE GENERAL DE GAULLE 78500 SARTROUVILLE",
        "code_postal": "78500", "liste_enseignes": None,
        "date_creation": "1989-05-31",
    }],
}


def _fake_fetch(responses):
    """Fetch factice : {url: réponse}. Enregistre les params reçus."""
    calls = []

    def fetch(url, params):
        calls.append((url, dict(params)))
        return responses.get(url, {})

    fetch.calls = calls
    return fetch


def test_geocode_returns_coords_above_score_threshold():
    import app.ingestion.enrichment.siret_matcher as sm
    ban = {"features": [{"geometry": {"coordinates": [2.1912, 48.9442]},
                         "properties": {"label": "143 Avenue General de Gaulle 78500 Sartrouville",
                                        "score": 0.7}}]}
    fetch = _fake_fetch({sm.BAN_URL: ban})
    assert geocode("143 Av. du Général de Gaule Sartrouville", fetch) == (48.9442, 2.1912)


def test_geocode_rejects_low_score():
    # Cas l'Artémise : BAN géocode "Avenue d'Alsace" à 0.47 -> il faut refuser
    # (sinon on compare aux mauvais voisins).
    import app.ingestion.enrichment.siret_matcher as sm
    ban = {"features": [{"geometry": {"coordinates": [7.36, 48.08]},
                         "properties": {"label": "Avenue d'Alsace 68000 Colmar",
                                        "score": 0.47}}]}
    fetch = _fake_fetch({sm.BAN_URL: ban})
    assert geocode("10 rue des écoles, 68000, Colmar, Alsace", fetch) is None


def test_pick_by_address_single_chr_at_same_number_is_match():
    cands = _candidates([HIT_CAFETERIA, HIT_OCOIN])
    verdict, chosen = pick_by_address(cands, num="143", name="Tre Gusto")
    assert verdict == "match" and chosen[0]["siren"] == "989119201"


def test_pick_by_address_two_chr_at_same_number_is_ambiguous():
    # Cas Tre Gusto réel : SAR FOOD (2023) et OCOIN (2025) au 143 -> arbitre.
    cands = _candidates([HIT_CAFETERIA, HIT_SARFOOD, HIT_OCOIN])
    verdict, pool = pick_by_address(cands, num="143", name="Tre Gusto")
    assert verdict == "ambiguous" and {c["siren"] for c in pool} == {"948225982", "989119201"}


def test_pick_by_address_no_number_or_no_chr_is_none():
    cands = _candidates([HIT_CAFETERIA])
    assert pick_by_address(cands, num=None, name="X") == ("none", [])
    assert pick_by_address([], num="143", name="X") == ("none", [])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_siret_matcher.py -q`
Expected: FAIL — `ImportError: cannot import name 'geocode'`.

- [ ] **Step 3: Write the implementation**

Ajouter à `siret_matcher.py` (import `Tuple` depuis typing) :

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_siret_matcher.py -q`
Expected: PASS (15 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/ingestion/enrichment/siret_matcher.py backend/tests/test_siret_matcher.py
git commit -m "feat(matcher): chemin adresse BAN + /near_point + selection par numero de voie"
```

---

### Task 4: Arbitre LLM (fail-soft, client injectable)

**Files:**
- Modify: `backend/app/ingestion/enrichment/siret_matcher.py`
- Test: `backend/tests/test_siret_matcher.py`

**Interfaces:**
- Consumes: candidats normalisés (Task 2)
- Produces: `arbitrate(name, context, cands, client=None) -> Optional[str]` — SIREN choisi ou None (rejet/erreur/pas de client). `_openai_client() -> Optional[client]` (même convention que `instagram._openai_client`).

- [ ] **Step 1: Write the failing tests**

```python
from app.ingestion.enrichment.siret_matcher import arbitrate


class _FakeCompletion:
    def __init__(self, content):
        self.choices = [type("C", (), {"message": type("M", (), {"content": content})()})()]


class _FakeClient:
    """Client OpenAI factice qui renvoie un JSON fixe et capture le prompt."""
    def __init__(self, content):
        self._content = content
        self.last_messages = None
        outer = self

        class _Completions:
            def create(self, **kwargs):
                outer.last_messages = kwargs.get("messages")
                return _FakeCompletion(outer._content)

        self.chat = type("Chat", (), {"completions": _Completions()})()


def test_arbitrate_returns_chosen_siren():
    cands = _candidates([HIT_SARFOOD, HIT_OCOIN])
    client = _FakeClient('{"match_index": 1}')
    assert arbitrate("Tre Gusto", "resto italien qui démarre", cands, client) == "989119201"
    # Le contexte (bio) doit être dans le prompt : c'est lui qui évite Auréa.
    joined = " ".join(m["content"] for m in client.last_messages)
    assert "resto italien qui démarre" in joined


def test_arbitrate_null_means_no_match():
    cands = _candidates([HIT_AUREA])
    client = _FakeClient('{"match_index": null}')
    assert arbitrate("AURÉA", "bijoux, Portugal", cands, client) is None


def test_arbitrate_fails_soft():
    cands = _candidates([HIT_AUREA])
    assert arbitrate("AURÉA", "bio", cands, client=None) is None
    assert arbitrate("AURÉA", "bio", cands, _FakeClient("pas du json")) is None
    assert arbitrate("AURÉA", "bio", [], _FakeClient('{"match_index": 0}')) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_siret_matcher.py -q`
Expected: FAIL — `ImportError: cannot import name 'arbitrate'`.

- [ ] **Step 3: Write the implementation**

Ajouter (imports : `import json`, `import os`) :

```python
_ARBITER_SYSTEM = (
    "Tu relies un compte Instagram d'établissement CHR à son entreprise au "
    "registre Sirene. On te donne le nom Insta, un extrait de bio, et des "
    "candidats du registre (nom légal, enseignes, NAF, adresse, date de "
    "création). Le nom légal peut être SANS RAPPORT avec le nom commercial "
    "(holding, patronyme) : juge sur le faisceau adresse/NAF/récence/enseigne. "
    "Si le compte n'est manifestement PAS un établissement CHR français "
    "(marque, hors France, média), ou si aucun candidat ne colle : null. "
    'Réponds STRICTEMENT en JSON : {"match_index": <int|null>}.'
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
        f'| {c["adresse"]} | créé {c["date_creation"]}'
        for i, c in enumerate(cands)
    )
    user = (f"Compte Insta : {name}\nBio/contexte : {(context or '')[:300]}\n\n"
            f"Candidats registre :\n{listing}\n\n"
            'Format EXACT : {"match_index": <int|null>}')
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_siret_matcher.py -q`
Expected: PASS (18 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/ingestion/enrichment/siret_matcher.py backend/tests/test_siret_matcher.py
git commit -m "feat(matcher): arbitre LLM injectable et fail-soft"
```

---

### Task 5: Orchestration `match()` + `MatchResult`

**Files:**
- Modify: `backend/app/ingestion/enrichment/siret_matcher.py`
- Test: `backend/tests/test_siret_matcher.py`

**Interfaces:**
- Consumes: tout ce qui précède.
- Produces: `@dataclass MatchResult(siren, siret, naf, enseigne, confidence, method)` — `confidence ∈ {"haute","moyenne"}`, `method ∈ {"nom","adresse","arbitre"}` ; `match(name, city=None, postal=None, address=None, context=None, fetch=_http_get, llm_client=_USE_ENV) -> Optional[MatchResult]`. Sentinel `_USE_ENV` = résoudre le client OpenAI depuis l'env ; passer `llm_client=None` = explicitement SANS arbitre (tests déterministes, jamais d'appel réseau LLM). C'est la SEULE API publique consommée par le pipeline (Task 7).

- [ ] **Step 1: Write the failing tests**

```python
from app.ingestion.enrichment.siret_matcher import match
import app.ingestion.enrichment.siret_matcher as sm

_BAN_TREGUSTO = {"features": [{"geometry": {"coordinates": [2.1912, 48.9442]},
                               "properties": {"label": "143 Av 78500 Sartrouville",
                                              "score": 0.7}}]}


def test_match_by_name_with_geo():
    fetch = _fake_fetch({sm.SEARCH_URL: {"results": [HIT_MAIRIE, HIT_MOURE]}})
    got = match("LE MOURE ROUGE - CANNES 🛟", city="Cannes", fetch=fetch)
    assert got is not None
    assert (got.siren, got.method, got.confidence) == ("899355770", "nom", "haute")


def test_match_by_address_via_arbiter():
    # Cas Tre Gusto : nom inconnu au registre, 2 CHR au 143 -> arbitre -> OCOIN.
    fetch = _fake_fetch({
        sm.SEARCH_URL: {"results": []},
        sm.BAN_URL: _BAN_TREGUSTO,
        sm.NEAR_URL: {"results": [HIT_CAFETERIA, HIT_SARFOOD, HIT_OCOIN]},
    })
    got = match("Tre Gusto", city="Sartrouville",
                address="143 Av. du Général de Gaule Sartrouville",
                context="resto italien qui démarre",
                fetch=fetch, llm_client=_FakeClient('{"match_index": 1}'))
    assert got is not None
    assert (got.siren, got.siret, got.method) == ("989119201", "98911920100011", "arbitre")


def test_match_single_chr_at_number_without_llm():
    fetch = _fake_fetch({
        sm.SEARCH_URL: {"results": []},
        sm.BAN_URL: _BAN_TREGUSTO,
        sm.NEAR_URL: {"results": [HIT_CAFETERIA, HIT_OCOIN]},
    })
    got = match("Tre Gusto", address="143 Av. du Général de Gaule",
                fetch=fetch, llm_client=None)
    assert got is not None and (got.method, got.confidence) == ("adresse", "moyenne")


def test_match_name_only_without_geo_needs_arbiter():
    # Piège Auréa : sans LLM -> None (conservateur) ; avec LLM qui rejette -> None.
    fetch = _fake_fetch({sm.SEARCH_URL: {"results": [HIT_AUREA]}})
    assert match("AURÉA", fetch=fetch, llm_client=None) is None
    assert match("AURÉA", context="bijoux, Portugal", fetch=fetch,
                 llm_client=_FakeClient('{"match_index": null}')) is None


def test_match_returns_none_when_nothing():
    fetch = _fake_fetch({})
    assert match("MOKA", city="Paris", fetch=fetch) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_siret_matcher.py -q`
Expected: FAIL — `ImportError: cannot import name 'match'`.

- [ ] **Step 3: Write the implementation**

Ajouter (import `from dataclasses import dataclass`) :

```python
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
    enseigne = (cand["enseignes"].split() and cand["enseignes"]) or cand["nom"] or None
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_siret_matcher.py -q`
Expected: PASS (23 tests). Vérifier aussi la non-régression globale : `python -m pytest tests/ -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/ingestion/enrichment/siret_matcher.py backend/tests/test_siret_matcher.py
git commit -m "feat(matcher): orchestration match() nom -> adresse -> arbitre"
```

---

### Task 6: Éval matching (groundtruth `expected_siren` + fixtures record/replay)

**Files:**
- Modify: `backend/app/ingestion/eval/instagram_groundtruth.csv`
- Create: `backend/app/ingestion/eval/match_eval.py`
- Create: `backend/app/ingestion/eval/fixtures/match/` (peuplé par `--record`)

**Interfaces:**
- Consumes: `match`, `Fetch` (Task 5) ; snapshots + CSV existants.
- Produces: CLI `python -m app.ingestion.eval.match_eval` (offline, fixtures) / `--record` (live, écrit les fixtures) / `--live` (sans fixtures). Métriques : rappel des matchs attendus, faux merges (gate = 0).

- [ ] **Step 1: Ajouter la colonne `expected_siren` au CSV**

Remplacer le contenu de `instagram_groundtruth.csv` par (SIREN validés lors du test live du 2026-07-04 ; vide = no-match attendu ; `chezgratien` volontairement vide car incertain — non scoré) :

```csv
handle,name,label,confidence,provenance,rationale,expected_siren
loumasrestaurant,Lou Mas,opening,high,opened_this_session,"2 posts, bio 'ouverture prochainement Printemps/Ete 2026', decor pas encore pose",992408872
chezgratien_hotelbistrospa,Chez Gratien,opening,high,opened_this_session,"Bio 'Juillet 2026', highlights Travaux + Recrutement, pre-ouverture confirmee",
tregusto_sartrouville,Tres Gusto,opening,med,opened_this_session,"4 posts / 58 abonnes, resto italien qui demarre, pas d'horaires ni resa",989119201
brasseriedelafontainelourmarin,Brasserie de la Fontaine,opening,med,prior_run,"13 posts, highlights SOON = pre-ouverture (non rouvert perso cette session)",
imagine.trouville,Imagine,just_opened,med,opened_this_session,"Horaires affiches (mer-dim) = tout juste ouvert/imminent, petit format artisanal",105127385
monica_stgermain,Monica,just_opened,low,prior_run,"4 posts mais horaires deja affiches = statut ouverture incertain",
giorgina_restaurant,Giorgina,established,low,opened_this_session,"15 posts / 662 abonnes, aucun signal d'ouverture explicite, ambigu",
lartemise_colmar,l'Artemise,established,high,opened_this_session,"682 posts, horaires affiches, salon de the etabli, decor fige",841751183
osabaita,Osabaita,established,high,opened_this_session,"101 posts, reservation active (tel), deja ouvert et decore",
lemourerouge_cannes,Le Moure Rouge,established,high,opened_this_session,"193 posts, ouvert 7j/7, reouverture saisonniere, decor en place",899355770
calaroya_plage,Cala Roya,established,high,opened_this_session,"46 posts, 'open everyday', menu actif = deja ouvert",
lamerpaulettetrouville,La Mer Paulette,established,high,opened_this_session,"172 posts, site + menu + brunch, restaurant etabli rue des Bains",909471096
villa.henriette_cabourg,Villa Henriette,established,med,opened_this_session,"48 posts, site de reservation actif, bio au present 'adresse charmante de 20 cles'",
cafe_mokaparis,MOKA,chain_multisite,high,opened_this_session,"3 adresses en bio (Champs/Opera/Galeries Lafayette), 'open everyday', chaine etablie",
cherescousinesbagels,Cheres Cousines,chain_multisite,med,prior_run,"Marque etablie multi-sites (Lyon 6 + Paris 11), nouvelle adresse = decor replique",994929917
lemarcchiato,Le Marcchiato,chain_multisite,med,prior_run,"139 posts, 5k abonnes, 2e adresse d'une marque etablie, decor centralise probable",979892619
un_lieu_une_ame_,Un Lieu Une Ame,not_venue,high,opened_this_session,"Agence de design/storytelling (2 creatrices), pas un etablissement CHR",
maisonaurea,Aurea,not_venue,high,opened_this_session,"Marque de bijoux au Portugal, hors secteur et hors France - piege OCR",
maisonsaintaubain,Maison Saint-Aubain,not_venue,high,opened_this_session,"Boucherie-fromagerie belge (.be), hors secteur CHR et hors France",
chickntikka94,Chick'n Tikka,noise,high,opened_this_session,"Fast-food, 2 posts / 1 abonne, pas de bio, quasi mort - sans valeur",100445048
```

Note : `chickntikka94` et `loumasrestaurant` n'ont ni géo ni adresse — leur match passe par l'arbitre (LLM live requis) ; l'éval les compte à part si `OPENAI_API_KEY` absent.

- [ ] **Step 2: Write `match_eval.py`**

```python
# backend/app/ingestion/eval/match_eval.py
"""Éval du matching Insta -> SIREN sur les snapshots figés (CLI).

  python -m app.ingestion.eval.match_eval            # offline (fixtures HTTP)
  python -m app.ingestion.eval.match_eval --record   # live + écrit les fixtures
  python -m app.ingestion.eval.match_eval --live     # live sans fixtures

HTTP (Sirene/BAN) figé en fixtures ; l'arbitre LLM tourne live si clé présente
(température 0), comme l'éval de classification. Gates : 0 faux merge, rappel
des matchs attendus affiché (référence : 9 attendus au 2026-07-04).
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, Optional

from ..enrichment import siret_matcher as sm
from .run import load_groundtruth, load_snapshot

ROOT = Path(__file__).resolve().parent
FIX_DIR = ROOT / "fixtures" / "match"

# Ville probable par handle (ce que discover/locationName fournirait en prod).
CITY_HINTS = {
    "tregusto_sartrouville": "Sartrouville",
    "brasseriedelafontainelourmarin": "Lourmarin",
    "imagine.trouville": "Trouville-sur-Mer",
    "monica_stgermain": "Paris",
    "giorgina_restaurant": "Paris",
    "lartemise_colmar": "Colmar",
    "lemourerouge_cannes": "Cannes",
    "lamerpaulettetrouville": "Trouville-sur-Mer",
    "villa.henriette_cabourg": "Cabourg",
    "cafe_mokaparis": "Paris",
    "cherescousinesbagels": "Paris",
    "lemarcchiato": "Vienne",
}

_ADDR_RE = re.compile(
    r"\b\d{1,4}\s?(?:bis|ter)?\s?,?\s+(?:rue|avenue|av\.?|boulevard|bd\.?|place|"
    r"quai|chemin|all[ée]e|impasse|cours|route|passage|promenade)\s+"
    r"[a-zA-ZÀ-ÿ'’\- ]{3,45}", re.IGNORECASE)


def _key(url: str, params: Dict[str, Any]) -> str:
    return f"{url}?{json.dumps(params, sort_keys=True, ensure_ascii=False)}"


def _recording_fetch(store: Dict[str, Any]):
    def fetch(url, params):
        data = sm._http_get(url, params)
        store[_key(url, params)] = data
        return data
    return fetch


def _replay_fetch(store: Dict[str, Any]):
    def fetch(url, params):
        return store.get(_key(url, params), {})
    return fetch


def _inputs_from_snapshot(handle: str, snap: dict) -> Dict[str, Optional[str]]:
    """Reconstruit les entrées que le pipeline aurait : nom, ville, adresse, bio."""
    ba = snap.get("businessAddress") or {}
    ba_city = (ba.get("city_name") or "").split(",")[0].strip() or None
    addr = None
    if ba.get("street_address"):
        addr = ", ".join(filter(None, [ba.get("street_address"),
                                       ba.get("zip_code"), ba_city]))
    else:
        bio = snap.get("biography") or ""
        caps = " ".join((p.get("caption") or "") for p in (snap.get("latestPosts") or [])[:8])
        found = _ADDR_RE.findall(bio) or _ADDR_RE.findall(caps)
        city = CITY_HINTS.get(handle) or ba_city or ""
        if found:
            addr = f"{found[0]} {city}".strip()
    return {
        "name": snap.get("fullName") or handle,
        "city": CITY_HINTS.get(handle) or ba_city,
        "address": addr,
        "context": (snap.get("biography") or "")[:300],
    }


def run_match_eval(mode: str = "offline") -> dict:
    rows = load_groundtruth()
    results = []
    for row in rows:
        handle = row["handle"].strip()
        expected = (row.get("expected_siren") or "").strip() or None
        snap = load_snapshot(handle)
        if snap is None:
            results.append({"handle": handle, "status": "no_snapshot",
                            "expected": expected, "got": None})
            continue
        fix_path = FIX_DIR / f"{handle}.json"
        if mode == "record":
            store: Dict[str, Any] = {}
            fetch = _recording_fetch(store)
        elif mode == "live":
            fetch = sm._http_get
        else:
            if not fix_path.exists():
                results.append({"handle": handle, "status": "no_fixture",
                                "expected": expected, "got": None})
                continue
            fetch = _replay_fetch(json.loads(fix_path.read_text(encoding="utf-8")))
        inputs = _inputs_from_snapshot(handle, snap)
        got = sm.match(fetch=fetch, **inputs)
        if mode == "record":
            FIX_DIR.mkdir(parents=True, exist_ok=True)
            fix_path.write_text(json.dumps(store, ensure_ascii=False, indent=1),
                                encoding="utf-8")
        got_siren = got.siren if got else None
        if expected and got_siren == expected:
            status = "ok_match"
        elif expected and got_siren is None:
            status = "missed"
        elif expected:
            status = "wrong_siren"
        elif got_siren:
            status = "false_merge"
        else:
            status = "ok_nomatch"
        results.append({"handle": handle, "status": status, "expected": expected,
                        "got": got_siren,
                        "method": got.method if got else None,
                        "confidence": got.confidence if got else None})

    n_expected = sum(1 for r in results if r["expected"])
    ok = sum(1 for r in results if r["status"] == "ok_match")
    false_merges = [r for r in results if r["status"] in ("false_merge", "wrong_siren")]
    return {"results": results, "n_expected": n_expected, "ok": ok,
            "false_merges": false_merges}


def main() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT.parents[2] / ".env")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="Éval matching Insta -> SIREN")
    parser.add_argument("--record", action="store_true")
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    mode = "record" if args.record else ("live" if args.live else "offline")
    rep = run_match_eval(mode)
    print("=" * 64)
    print(f"EVAL MATCHING ({mode}) — {rep['ok']}/{rep['n_expected']} matchs attendus retrouvés")
    print("=" * 64)
    for r in rep["results"]:
        print(f'  {r["status"]:<12} {r["handle"]:<32} attendu={r["expected"] or "-":<11}'
              f' obtenu={r["got"] or "-":<11} ({r["method"] or ""})')
    if rep["false_merges"]:
        print(f'\n!! FAUX MERGES ({len(rep["false_merges"])}) — GATE ROUGE, à corriger avant de continuer')
    print("=" * 64)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Enregistrer les fixtures (live, APIs gratuites)**

Run: `python -m app.ingestion.eval.match_eval --record`
Expected: tableau imprimé ; viser `>= 7/9` (loumas et chickntikka dépendent de l'arbitre LLM live ; sans `OPENAI_API_KEY` ils sortent `missed`) et **0 ligne `false_merge`/`wrong_siren`**. En particulier : `tregusto` ok via `arbitre` (ou `missed` sans clé — noter), `maisonaurea` en `ok_nomatch`, `cafe_mokaparis` en `ok_nomatch`.

Si un `false_merge` apparaît : STOP, diagnostiquer (probablement `_geo_consistent` ou `pick_by_address` trop laxiste), corriger, re-lancer. Ne pas élargir les seuils pour "faire passer".

- [ ] **Step 4: Rejouer offline pour valider les fixtures**

Run: `python -m app.ingestion.eval.match_eval`
Expected: mêmes statuts que le record (l'arbitre LLM reste live : de petites différences sur les cas `arbitre` sont tolérées et à noter).

- [ ] **Step 5: Commit**

```bash
git add backend/app/ingestion/eval/match_eval.py backend/app/ingestion/eval/instagram_groundtruth.csv backend/app/ingestion/eval/fixtures/
git commit -m "feat(eval): eval du matching SIREN avec fixtures record/replay + expected_siren"
```

---

### Task 7: Intégration pipeline (remplace `backfill_siren`) + suppression

**Files:**
- Modify: `backend/app/ingestion/instagram.py` (fonction `profile_enrich` : ajouter le `bio_snippet`)
- Modify: `backend/app/ingestion/pipeline.py:29` (import) et `pipeline.py:245-252` (appel dans `run_instagram`)
- Delete: `backend/app/ingestion/enrichment/backfill.py`
- Test: `backend/tests/test_siret_matcher.py`

**Interfaces:**
- Consumes: `match` (Task 5).
- Produces: `run_instagram` enrichit via `match(name, city, postal, address, context)` ; le lead porte `lead["bio_snippet"]` (posé par `profile_enrich`).

- [ ] **Step 1: Write the failing test**

```python
def test_pipeline_uses_matcher(monkeypatch):
    """run_instagram doit appeler siret_matcher.match (plus backfill_siren)."""
    import app.ingestion.pipeline as pl
    from app.ingestion.enrichment.siret_matcher import MatchResult

    calls = {}

    def fake_match(name, city=None, postal=None, address=None, context=None, **kw):
        calls["name"] = name
        return MatchResult(siren="989119201", siret="98911920100011",
                           naf="56.10C", enseigne="OCOIN",
                           confidence="moyenne", method="arbitre")

    monkeypatch.setattr(pl, "match_siret", fake_match)
    got = pl._match_lead({"handle": "x", "name": "Tre Gusto", "city": "Sartrouville",
                          "address": "143 Av. du Général de Gaule",
                          "bio_snippet": "resto italien"})
    assert calls["name"] == "Tre Gusto"
    assert got == {"siren": "989119201", "naf": "56.10C", "enseigne": "OCOIN"}


def test_match_lead_none_is_empty_dict():
    import app.ingestion.pipeline as pl
    assert pl._match_lead({"handle": "x", "name": "", "city": ""}) == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_siret_matcher.py -q`
Expected: FAIL — `AttributeError: ... has no attribute 'match_siret'` / `_match_lead`.

- [ ] **Step 3: Implement**

Dans `instagram.py`, fonction `profile_enrich`, juste avant `kept.append(c)` (après le bloc `website`), ajouter :

```python
        # Contexte pour l'arbitre du matching SIREN (pipeline).
        c["bio_snippet"] = (prof.get("biography") or "")[:300]
```

Dans `pipeline.py` :

1. Remplacer la ligne 29 `from .enrichment.backfill import backfill_siren` par :

```python
from .enrichment.siret_matcher import match as match_siret
```

2. Ajouter (près des helpers, avant `run_instagram`) :

```python
def _match_lead(lead: dict) -> dict:
    """Lead Insta -> {siren, naf, enseigne} via le matcher, ou {} (fail-soft).
    Remplace backfill_siren : mêmes clés consommées par run_instagram."""
    m = match_siret(
        name=lead.get("name") or "",
        city=lead.get("city"),
        address=lead.get("address"),
        context=lead.get("bio_snippet"),
    )
    if m is None:
        return {}
    return {"siren": m.siren, "naf": m.naf, "enseigne": m.enseigne}
```

3. Dans `run_instagram`, remplacer `bf = backfill_siren(lead["name"], lead["city"]) or {}` par :

```python
                bf = _match_lead(lead)
```

(le reste — `bf.get("enseigne")`, `bf.get("siren")`, `bf.get("naf")` — est inchangé).

4. Supprimer `backend/app/ingestion/enrichment/backfill.py` :

```bash
git rm backend/app/ingestion/enrichment/backfill.py
```

Vérifier qu'aucun autre import ne subsiste : `grep -rn "backfill" backend/app backend/tests` → seul `pipeline.py` (le helper `_match_lead`) doit apparaître, sinon corriger.

- [ ] **Step 4: Run the full gates**

Run: `python -m pytest tests/ -q`
Expected: PASS complet.

Run: `python -m app.ingestion.eval.match_eval`
Expected: mêmes résultats que Task 6 (le matcher n'a pas changé, c'est l'intégration).

Run: `python -m app.ingestion.eval.run`
Expected: précision/rappel IDENTIQUES à l'état avant la branche (cette brique ne touche pas la classification ; toute variation = régression à investiguer).

- [ ] **Step 5: Commit**

```bash
git add backend/app/ingestion/pipeline.py backend/app/ingestion/instagram.py backend/tests/test_siret_matcher.py
git commit -m "feat(pipeline): run_instagram passe au siret_matcher, retrait de backfill_siren"
```

---

## Hors périmètre (plans suivants)

Brique 2 (connecteur délta-Sirene), brique 3 (funnel v2 + cache `handle_verdicts`), brique 4 (watchlist/réconciliation) : un plan chacun, après validation de celui-ci. La colonne `siren_match_method` en base (traçabilité) est reportée à la brique 3 (qui touche déjà les modèles pour `handle_verdicts`).
