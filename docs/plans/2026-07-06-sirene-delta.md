# Délta-Sirene (brique 2) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Un connecteur « délta-Sirene » qui ingère chaque jour les nouveaux SIRET CHR (y compris les créations à date FUTURE = ouvertures pré-déclarées) via l'API Sirene INSEE, avec fusion par SIREN quand un lead Instagram corrobore, et persistance de la traçabilité du matching (siret/method/confidence).

**Architecture:** Nouveau connecteur `SireneDeltaConnector` (interface `Connector` existante) : requête `dateCreationEtablissement:[from TO to] AND periode(NAF ORs)` sur `api.insee.fr/api-sirene/3.11/siret`, pagination par curseur, throttle 2 s (30 req/min). Mapping pur vers `LeadCandidate` (gestion des `[ND]`, enseigne > dénomination, flag siège→extension). Fusion par SIREN dans `_process_candidate` : un lead entrant dont le SIREN existe déjà sous une autre source enrichit l'existant (Signal + corroboration + rescore) au lieu de créer un doublon.

**Tech Stack:** Python 3.9, requests, SQLModel/SQLite (migrations légères), pytest. API INSEE Sirene 3.11 (header `X-INSEE-Api-Key-Integration`, clé env `INSEE_API_KEY`, déjà dans `backend/.env`).

## Global Constraints

- Python 3.9 : `Optional[X]`/`Dict`/`List` de `typing`, jamais `X | None`.
- Fail-soft : pas de clé `INSEE_API_KEY` → `fetch()` renvoie `[]` sans erreur ; toute erreur HTTP → liste partielle ou vide, jamais d'exception qui remonte.
- Throttle INSEE : ≥ 2,1 s entre requêtes (limite 30 req/min).
- Syntaxe API vérifiée live le 2026-07-06 : `activitePrincipaleEtablissement` DOIT être sous `periode(...)` (sinon 400) ; `dateCreationEtablissement` supporte les plages `[A TO B]` et les dates futures ; pagination `curseur=*` → `header.curseurSuivant` (s'arrêter quand `curseurSuivant == curseur`) ; `nombre` max 1000.
- Codes NAF CHR (alignés `naf_classifier`) : `56.10A, 56.10B, 56.10C, 56.21Z, 56.30Z, 55.10Z, 55.20Z`.
- Docstrings/commentaires en français.
- Aucun appel réseau réel dans les tests (fetch injectable) ; UNE passe live bornée en gate final.
- Commandes `python`/`pytest` depuis `chr-signal-radar/backend` (`.venv\Scripts\python.exe`) ; `git` depuis la racine `chr-signal-radar/`.
- `python -m pytest tests/ -q` vert à la fin de CHAQUE tâche.

---

### Task 1: Client INSEE (requête, pagination, throttle) — `ingestion/insee.py`

**Files:**
- Create: `backend/app/ingestion/insee.py`
- Create: `backend/tests/test_sirene_delta.py`

**Interfaces:**
- Produces: `has_insee_key() -> bool` ; `build_query(date_from: date, date_to: date, naf_codes: Sequence[str], cp_prefixes: Optional[Sequence[str]]) -> str` (pure) ; `fetch_new_etablissements(date_from, date_to, naf_codes, cp_prefixes=None, limit=3000, fetch=None) -> List[dict]` (établissements bruts INSEE, paginés).

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_sirene_delta.py
"""Tests du connecteur delta-Sirene (INSEE) — brique 2 du pivot."""
from datetime import date

from app.ingestion.insee import build_query, fetch_new_etablissements

D1, D2 = date(2026, 6, 29), date(2026, 7, 5)
NAFS = ["56.10A", "56.10C"]


def test_build_query_periode_and_range():
    q = build_query(D1, D2, NAFS, None)
    # Verifie la syntaxe validee live le 2026-07-06 : plage + periode(...).
    assert "dateCreationEtablissement:[2026-06-29 TO 2026-07-05]" in q
    assert ("periode(activitePrincipaleEtablissement:56.10A"
            " OR activitePrincipaleEtablissement:56.10C)") in q
    assert " AND " in q


def test_build_query_cp_prefixes():
    q = build_query(D1, D2, NAFS, ["75", "92"])
    assert "(codePostalEtablissement:75* OR codePostalEtablissement:92*)" in q


class _FakeInsee:
    """Fetch factice paginee : rejoue des pages INSEE et enregistre les appels."""
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def __call__(self, url, params, headers):
        self.calls.append(dict(params))
        cur = params.get("curseur", "*")
        page = self.pages.get(cur, {"header": {"statut": 404}})
        return page


def _page(etabs, curseur, suivant, total):
    return {"header": {"statut": 200, "total": total, "curseur": curseur,
                       "curseurSuivant": suivant},
            "etablissements": etabs}


def test_fetch_paginates_with_curseur(monkeypatch):
    monkeypatch.setenv("INSEE_API_KEY", "test-key")
    fake = _FakeInsee({
        "*": _page([{"siret": "1"}, {"siret": "2"}], "*", "CUR2", 3),
        "CUR2": _page([{"siret": "3"}], "CUR2", "CUR2", 3),  # suivant == curseur -> fin
    })
    got = fetch_new_etablissements(D1, D2, NAFS, fetch=fake)
    assert [e["siret"] for e in got] == ["1", "2", "3"]
    assert fake.calls[0]["curseur"] == "*"
    assert fake.calls[1]["curseur"] == "CUR2"


def test_fetch_respects_limit_and_fails_soft(monkeypatch):
    monkeypatch.setenv("INSEE_API_KEY", "test-key")
    fake = _FakeInsee({"*": _page([{"siret": str(i)} for i in range(5)], "*", "N", 99),
                       "N": {"header": {"statut": 500}}})
    assert len(fetch_new_etablissements(D1, D2, NAFS, limit=2, fetch=fake)) == 2
    # Erreur en page 2 -> on garde la premiere page (fail-soft, jamais d'exception).
    got = fetch_new_etablissements(D1, D2, NAFS, limit=100, fetch=fake)
    assert [e["siret"] for e in got] == ["0", "1", "2", "3", "4"]


def test_fetch_without_key_is_noop(monkeypatch):
    monkeypatch.delenv("INSEE_API_KEY", raising=False)
    fake = _FakeInsee({})
    assert fetch_new_etablissements(D1, D2, NAFS, fetch=fake) == []
    assert fake.calls == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_sirene_delta.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.ingestion.insee'`.

- [ ] **Step 3: Write the implementation**

```python
# backend/app/ingestion/insee.py
"""Client API Sirene INSEE (delta des nouveaux SIRET) — [BRIQUE 2].

Syntaxe validee live (2026-07-06) : les champs historises comme
`activitePrincipaleEtablissement` DOIVENT etre requetes via `periode(...)`
(sinon 400 « Erreur de syntaxe ») ; `dateCreationEtablissement` accepte les
plages ET les dates futures (creations pre-declarees = ouvertures annoncees
au registre). Pagination par curseur ; fin quand curseurSuivant == curseur.
Cle gratuite requise (portail-api.insee.fr) : env `INSEE_API_KEY`.
Fail-soft : pas de cle / erreur -> [] ou liste partielle, jamais d'exception.
"""
from __future__ import annotations

import os
import time
from datetime import date
from typing import Any, Callable, Dict, List, Optional, Sequence

import requests

SIRET_URL = "https://api.insee.fr/api-sirene/3.11/siret"
_MIN_INTERVAL = 2.1  # 30 req/min
_PAGE_SIZE = 1000    # max autorise par l'API
_last_call = [0.0]

InseeFetch = Callable[[str, Dict[str, Any], Dict[str, str]], Dict[str, Any]]


def has_insee_key() -> bool:
    return bool(os.getenv("INSEE_API_KEY"))


def build_query(date_from: date, date_to: date, naf_codes: Sequence[str],
                cp_prefixes: Optional[Sequence[str]] = None) -> str:
    """Construit le parametre q (pure, testable)."""
    date_part = f"dateCreationEtablissement:[{date_from.isoformat()} TO {date_to.isoformat()}]"
    naf_part = " OR ".join(f"activitePrincipaleEtablissement:{c}" for c in naf_codes)
    parts = [date_part, f"periode({naf_part})"]
    if cp_prefixes:
        cp_part = " OR ".join(f"codePostalEtablissement:{p}*" for p in cp_prefixes)
        parts.append(f"({cp_part})")
    return " AND ".join(parts)


def _http_get(url: str, params: Dict[str, Any], headers: Dict[str, str]) -> Dict[str, Any]:
    """GET throttle (30 req/min), fail-soft {}."""
    wait = _MIN_INTERVAL - (time.monotonic() - _last_call[0])
    if wait > 0:
        time.sleep(wait)
    _last_call[0] = time.monotonic()
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return {}


def fetch_new_etablissements(
    date_from: date, date_to: date, naf_codes: Sequence[str],
    cp_prefixes: Optional[Sequence[str]] = None,
    limit: int = 3000,
    fetch: Optional[InseeFetch] = None,
) -> List[Dict[str, Any]]:
    """Tous les etablissements crees dans la fenetre (pagination curseur)."""
    key = os.getenv("INSEE_API_KEY")
    if not key:
        return []
    fetch = fetch or _http_get
    headers = {"X-INSEE-Api-Key-Integration": key}
    q = build_query(date_from, date_to, naf_codes, cp_prefixes)
    out: List[Dict[str, Any]] = []
    curseur = "*"
    while len(out) < limit:
        nombre = min(_PAGE_SIZE, limit - len(out))
        data = fetch(SIRET_URL, {"q": q, "nombre": nombre, "curseur": curseur}, headers)
        header = data.get("header") or {}
        if header.get("statut") != 200:
            break  # fail-soft : on garde ce qu'on a
        out.extend(data.get("etablissements") or [])
        suivant = header.get("curseurSuivant")
        if not suivant or suivant == curseur:
            break
        curseur = suivant
    return out[:limit]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_sirene_delta.py -q`
Expected: PASS (5 tests). Puis `python -m pytest tests/ -q` → tout vert.

- [ ] **Step 5: Commit**

```bash
git add backend/app/ingestion/insee.py backend/tests/test_sirene_delta.py
git commit -m "feat(delta): client API Sirene INSEE (plage de dates, periode(), curseur, fail-soft)"
```

---

### Task 2: Mapping pur établissement INSEE → `LeadCandidate`

**Files:**
- Create: `backend/app/ingestion/sirene_delta.py`
- Test: `backend/tests/test_sirene_delta.py`

**Interfaces:**
- Consumes: `LeadCandidate` (`ingestion/base.py`), `classify_naf` (`enrichment/naf_classifier.py`).
- Produces: `CHR_NAF_CODES` (liste) ; `map_etablissement(etab: dict, today: date) -> Optional[LeadCandidate]` (pure — None si inexploitable) ; `_best_name(etab) -> Optional[str]`.

- [ ] **Step 1: Write the failing tests**

Ajouter à `backend/tests/test_sirene_delta.py` (formes réelles observées le 2026-07-06) :

```python
from app.ingestion.sirene_delta import CHR_NAF_CODES, map_etablissement

TODAY = date(2026, 7, 6)

# Etablissement diffusible, societe, cree (forme reelle API 3.11).
ETAB_OK = {
    "siren": "105506737", "siret": "10550673700029",
    "dateCreationEtablissement": "2026-07-01", "etablissementSiege": False,
    "statutDiffusionEtablissement": "O",
    "uniteLegale": {"denominationUniteLegale": "ACTIVE FOOD CONCEPT LE PUY",
                    "categorieJuridiqueUniteLegale": "5710",
                    "prenom1UniteLegale": None, "nomUniteLegale": None},
    "adresseEtablissement": {"numeroVoieEtablissement": "13",
                             "typeVoieEtablissement": "ROUTE",
                             "libelleVoieEtablissement": "DE COUBON",
                             "codePostalEtablissement": "43700",
                             "libelleCommuneEtablissement": "BRIVES-CHARENSAC"},
    "periodesEtablissement": [{"activitePrincipaleEtablissement": "56.10B",
                               "etatAdministratifEtablissement": "A",
                               "enseigne1Etablissement": None,
                               "denominationUsuelleEtablissement": None}],
}
# Personne physique non-diffusible ([ND] partout, pas d'enseigne).
ETAB_ND = {
    "siren": "100731280", "siret": "10073128000010",
    "dateCreationEtablissement": "2026-07-01", "etablissementSiege": True,
    "statutDiffusionEtablissement": "P",
    "uniteLegale": {"denominationUniteLegale": "[ND]", "nomUniteLegale": "[ND]",
                    "prenom1UniteLegale": "[ND]",
                    "categorieJuridiqueUniteLegale": "1000"},
    "adresseEtablissement": {"codePostalEtablissement": "75011",
                             "libelleCommuneEtablissement": "PARIS"},
    "periodesEtablissement": [{"activitePrincipaleEtablissement": "56.10C",
                               "etatAdministratifEtablissement": "A",
                               "enseigne1Etablissement": None}],
}


def test_map_etablissement_nominal():
    cand = map_etablissement(ETAB_OK, TODAY)
    assert cand is not None
    assert cand.source == "sirene" and cand.source_ref == "10550673700029"
    assert cand.siren == "105506737" and cand.naf == "56.10B"
    assert cand.establishment_name == "ACTIVE FOOD CONCEPT LE PUY"
    assert cand.city == "Brives-Charensac"
    assert cand.address == "13 ROUTE DE COUBON, 43700 Brives-Charensac"
    assert cand.main_signal == "ouverture prochaine"
    assert cand.activity_start_date == date(2026, 7, 1)
    # Etablissement secondaire d'une societe = extension multi-sites.
    assert "extension multi-sites" in cand.secondary_signals


def test_map_enseigne_prime_sur_denomination():
    etab = {**ETAB_OK, "periodesEtablissement": [{
        **ETAB_OK["periodesEtablissement"][0], "enseigne1Etablissement": "CHEZ LUCIE"}]}
    cand = map_etablissement(etab, TODAY)
    assert cand.establishment_name == "CHEZ LUCIE"


def test_map_nd_sans_enseigne_est_ecarte():
    assert map_etablissement(ETAB_ND, TODAY) is None


def test_map_nd_avec_enseigne_est_garde():
    etab = {**ETAB_ND, "periodesEtablissement": [{
        **ETAB_ND["periodesEtablissement"][0], "enseigne1Etablissement": "SNACK 11E"}]}
    cand = map_etablissement(etab, TODAY)
    assert cand is not None and cand.establishment_name == "SNACK 11E"


def test_map_creation_future_marquee_pre_declaree():
    etab = {**ETAB_OK, "dateCreationEtablissement": "2026-09-15"}
    cand = map_etablissement(etab, TODAY)
    # La date declaree AU FUTUR = ouverture annoncee au registre (signal fort).
    assert "2026-09-15" in (cand.proof_text or "")
    assert "pré-déclarée" in (cand.proof_text or "")


def test_map_etat_ferme_est_ecarte():
    etab = {**ETAB_OK, "periodesEtablissement": [{
        **ETAB_OK["periodesEtablissement"][0], "etatAdministratifEtablissement": "F"}]}
    assert map_etablissement(etab, TODAY) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_sirene_delta.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.ingestion.sirene_delta'`.

- [ ] **Step 3: Write the implementation**

```python
# backend/app/ingestion/sirene_delta.py
"""Connecteur delta-Sirene : nouveaux SIRET CHR -> LeadCandidate — [BRIQUE 2].

Colonne vertebrale du pivot inventaire (docs/inventory-pivot-design.md) :
l'immatriculation etant obligatoire AVANT l'ouverture, le delta quotidien des
nouveaux etablissements NAF 55/56 donne un recall ~100 % sur les ouvertures
(~80/jour France, mesure 2026-07-06), y compris les creations a date FUTURE
(ouvertures pre-declarees) et les etablissements secondaires (extension de
chaines, invisibles dans BODACC).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from .base import Connector, LeadCandidate
from .enrichment.naf_classifier import classify_naf
from .insee import fetch_new_etablissements

# Codes NAF CHR — memes codes que naf_classifier (le NAF fait autorite).
CHR_NAF_CODES = ["56.10A", "56.10B", "56.10C", "56.21Z", "56.30Z", "55.10Z", "55.20Z"]
# Horizon des creations pre-declarees (date future au registre).
FUTURE_HORIZON_DAYS = 120


def _nd(value: Optional[str]) -> Optional[str]:
    """Neutralise les champs proteges '[ND]' (statut diffusion partielle)."""
    v = (value or "").strip()
    return None if not v or v == "[ND]" else v


def _title(value: Optional[str]) -> str:
    return (value or "").strip().title()


def _best_name(etab: Dict[str, Any]) -> Optional[str]:
    """Enseigne > denomination usuelle > denomination uL > prenom+nom.
    None si tout est vide/[ND] (personne physique non-diffusible)."""
    per = (etab.get("periodesEtablissement") or [{}])[0]
    ul = etab.get("uniteLegale") or {}
    for cand in (
        per.get("enseigne1Etablissement"),
        per.get("denominationUsuelleEtablissement"),
        ul.get("denominationUniteLegale"),
    ):
        if _nd(cand):
            return _nd(cand)
    prenom, nom = _nd(ul.get("prenom1UniteLegale")), _nd(ul.get("nomUniteLegale"))
    if prenom and nom:
        return f"{prenom.title()} {nom.title()}"
    return None


def _address(etab: Dict[str, Any]) -> Tuple[str, str]:
    """-> (adresse complete, ville). Champs adresse non historises."""
    adr = etab.get("adresseEtablissement") or {}
    street = " ".join(filter(None, [
        (adr.get("numeroVoieEtablissement") or "").strip(),
        (adr.get("typeVoieEtablissement") or "").strip(),
        (adr.get("libelleVoieEtablissement") or "").strip(),
    ])).strip()
    city = _title(adr.get("libelleCommuneEtablissement"))
    cp = (adr.get("codePostalEtablissement") or "").strip()
    full = ", ".join(filter(None, [street, " ".join(filter(None, [cp, city]))]))
    return full, city


def map_etablissement(etab: Dict[str, Any], today: date) -> Optional[LeadCandidate]:
    """Etablissement INSEE brut -> LeadCandidate, ou None si inexploitable
    (ferme, hors CHR, ou anonyme [ND] sans enseigne). Fonction PURE."""
    per = (etab.get("periodesEtablissement") or [{}])[0]
    if (per.get("etatAdministratifEtablissement") or "A") != "A":
        return None
    naf = per.get("activitePrincipaleEtablissement")
    if not (naf and classify_naf(naf)):
        return None
    name = _best_name(etab)
    if not name:
        return None  # injoignable ET inmatchable : sans valeur commerciale
    created = _ymd(etab.get("dateCreationEtablissement"))
    address, city = _address(etab)

    secondary: List[str] = []
    ul = etab.get("uniteLegale") or {}
    if etab.get("etablissementSiege") is False:
        # Nouvel etablissement d'une societe existante = expansion multi-sites
        # (invisible dans BODACC — un des apports du delta).
        secondary.append("extension multi-sites")

    if created and created > today:
        proof = (f"Création d'établissement pré-déclarée au registre pour le "
                 f"{created.isoformat()} (NAF {naf}).")
    else:
        proof = (f"Établissement créé le {created.isoformat() if created else '?'} "
                 f"au registre Sirene (NAF {naf}).")

    return LeadCandidate(
        source="sirene",
        source_ref=etab.get("siret") or "",
        establishment_name=name,
        city=city or "",
        address=address,
        main_signal="ouverture prochaine",
        secondary_signals=secondary,
        detection_date=today,
        activity_start_date=created,
        classification_text=name,
        siren=etab.get("siren"),
        naf=naf,
        proof_text=proof,
        raw=etab,
    )


def _ymd(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None
```

(La classe `SireneDeltaConnector` arrive en Task 3 — ce module reste importable seul pour les tests purs.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_sirene_delta.py -q` → PASS (11 tests). Si `LeadCandidate` n'accepte pas un des kwargs utilisés, vérifier `base.py` et adapter le mapping (PAS le dataclass).

- [ ] **Step 5: Commit**

```bash
git add backend/app/ingestion/sirene_delta.py backend/tests/test_sirene_delta.py
git commit -m "feat(delta): mapping pur etablissement INSEE -> LeadCandidate (ND, enseigne, pre-declare, extension)"
```

---

### Task 3: `SireneDeltaConnector` + enregistrement pipeline/CLI

**Files:**
- Modify: `backend/app/ingestion/sirene_delta.py` (ajout de la classe)
- Modify: `backend/app/ingestion/pipeline.py` (`CONNECTORS`, import)
- Test: `backend/tests/test_sirene_delta.py`

**Interfaces:**
- Consumes: `fetch_new_etablissements` (Task 1), `map_etablissement` (Task 2), ABC `Connector`.
- Produces: `SireneDeltaConnector(Connector)` avec `name="sirene"`, `fetch(since_days=7, limit=3000, departments=None, since_date=None, **_) -> List[dict]`, `to_candidates(records) -> List[LeadCandidate]`, attribut `last_total_count` (comme BODACC, pour les stats de troncature). Enregistré : `CONNECTORS = {"bodacc": ..., "sirene": SireneDeltaConnector}`.

- [ ] **Step 1: Write the failing tests**

```python
def test_connector_fetch_window_and_future(monkeypatch):
    """La fenetre couvre [today-since_days ; today+FUTURE_HORIZON_DAYS] :
    le passe recent ET les ouvertures pre-declarees."""
    import app.ingestion.sirene_delta as sd
    captured = {}

    def fake_fetch(date_from, date_to, naf_codes, cp_prefixes=None, limit=3000, fetch=None):
        captured.update(date_from=date_from, date_to=date_to,
                        naf_codes=list(naf_codes), cp=cp_prefixes, limit=limit)
        return [dict(ETAB_OK)]

    monkeypatch.setattr(sd, "fetch_new_etablissements", fake_fetch)
    conn = sd.SireneDeltaConnector()
    records = conn.fetch(since_days=7, limit=500, departments=["75", "92"])
    assert len(records) == 1 and conn.last_total_count == 1
    assert (captured["date_to"] - captured["date_from"]).days == 7 + sd.FUTURE_HORIZON_DAYS
    assert captured["naf_codes"] == sd.CHR_NAF_CODES
    assert captured["cp"] == ["75", "92"] and captured["limit"] == 500


def test_connector_to_candidates_filters_unusable():
    import app.ingestion.sirene_delta as sd
    conn = sd.SireneDeltaConnector()
    cands = conn.to_candidates([dict(ETAB_OK), dict(ETAB_ND)])
    assert len(cands) == 1 and cands[0].source_ref == "10550673700029"


def test_connector_registered_in_pipeline():
    from app.ingestion.pipeline import get_connector
    conn = get_connector("sirene")
    assert conn.name == "sirene"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_sirene_delta.py -q`
Expected: FAIL — `AttributeError: ... no attribute 'SireneDeltaConnector'`.

- [ ] **Step 3: Write the implementation**

Dans `sirene_delta.py` :

```python
class SireneDeltaConnector(Connector):
    """Delta des nouveaux SIRET CHR (INSEE). `departments` = prefixes de CP.
    La fenetre remonte de `since_days` ET s'etend a +FUTURE_HORIZON_DAYS
    (creations pre-declarees). `since_date` (curseur incremental) prime sur
    since_days quand fourni."""
    name = "sirene"

    def __init__(self) -> None:
        self.last_total_count = 0

    def fetch(self, since_days: int = 7, limit: int = 3000,
              departments: Optional[List[str]] = None,
              since_date: Optional[date] = None, **_: Any) -> List[Dict[str, Any]]:
        today = date.today()
        date_from = since_date or (today - timedelta(days=since_days or 7))
        date_to = today + timedelta(days=FUTURE_HORIZON_DAYS)
        records = fetch_new_etablissements(
            date_from, date_to, CHR_NAF_CODES,
            cp_prefixes=departments, limit=limit,
        )
        self.last_total_count = len(records)
        return records

    def to_candidates(self, records: List[Dict[str, Any]]) -> List[LeadCandidate]:
        today = date.today()
        out: List[LeadCandidate] = []
        for etab in records:
            cand = map_etablissement(etab, today)
            if cand:
                out.append(cand)
        return out
```

Dans `pipeline.py` : ajouter `from .sirene_delta import SireneDeltaConnector` près de l'import Bodacc, et :

```python
CONNECTORS = {
    "bodacc": BodaccConnector,
    "sirene": SireneDeltaConnector,
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/ -q` → tout vert. Vérifier au passage que la CLI accepte la source : `python -m app.ingestion.run --mode window --source sirene --limit 1 --no-enrich` doit tourner sans crash (elle fera un appel live minimal — clé présente — et c'est OK ici : un seul appel borné).

- [ ] **Step 5: Commit**

```bash
git add backend/app/ingestion/sirene_delta.py backend/app/ingestion/pipeline.py backend/tests/test_sirene_delta.py
git commit -m "feat(delta): SireneDeltaConnector enregistre (source 'sirene', fenetre passee+future)"
```

---

### Task 4: Persistance de la traçabilité du matching (siret, method, confidence)

Dette d'interface identifiée en revue finale de la brique 1 : `MatchResult.siret/confidence/method` sont calculés puis jetés, or la corroboration (Task 5) fusionne par SIREN/SIRET et l'audit qualité en a besoin.

**Files:**
- Modify: `backend/app/models.py` (3 colonnes : `siret`, `siren_match_method`, `siren_match_confidence`)
- Modify: `backend/app/database.py` (3 entrées de migration légère)
- Modify: `backend/app/ingestion/base.py` (champs `siret`, `siren_match_method`, `siren_match_confidence` sur `LeadCandidate`)
- Modify: `backend/app/ingestion/pipeline.py` (`_match_lead` renvoie aussi siret/method/confidence ; `run_instagram` les pose sur le candidat ; `_process_candidate` les persiste ; `sirene_delta.map_etablissement` pose `siret=source_ref`)
- Test: `backend/tests/test_sirene_delta.py`

**Interfaces:**
- Produces: `Opportunity.siret: Optional[str]`, `Opportunity.siren_match_method: Optional[str]`, `Opportunity.siren_match_confidence: Optional[str]` ; `_match_lead(lead) -> dict` renvoie désormais `{siren, naf, enseigne, siret, method, confidence}` (clés absentes si pas de match).

- [ ] **Step 1: Write the failing tests**

```python
def test_match_lead_exposes_tracabilite(monkeypatch):
    import app.ingestion.pipeline as pl
    from app.ingestion.enrichment.siret_matcher import MatchResult

    monkeypatch.setattr(pl, "match_siret", lambda **kw: MatchResult(
        siren="989119201", siret="98911920100011", naf="56.10C",
        enseigne="OCOIN", confidence="moyenne", method="arbitre"))
    got = pl._match_lead({"handle": "x", "name": "Tre Gusto", "city": "Sartrouville"})
    assert got["siret"] == "98911920100011"
    assert got["method"] == "arbitre" and got["confidence"] == "moyenne"


def test_process_candidate_persists_tracabilite(tmp_path):
    from sqlmodel import SQLModel, Session, create_engine, select
    from app.models import Opportunity
    from app.ingestion.base import LeadCandidate
    from app.ingestion.pipeline import _process_candidate, IngestStats
    from datetime import date as _d

    engine = create_engine(f"sqlite:///{tmp_path/'t.db'}")
    SQLModel.metadata.create_all(engine)
    cand = LeadCandidate(
        source="sirene", source_ref="10550673700029",
        establishment_name="ACTIVE FOOD CONCEPT", city="Brives-Charensac",
        main_signal="ouverture prochaine", detection_date=_d(2026, 7, 6),
        classification_text="restaurant", establishment_type="restaurant",
        siren="105506737", naf="56.10B", siret="10550673700029",
    )
    with Session(engine) as s:
        _process_candidate(s, cand, IngestStats(source="sirene"), set(), None)
        s.commit()
        opp = s.exec(select(Opportunity)).first()
        assert opp.siret == "10550673700029"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_sirene_delta.py -q`
Expected: FAIL — `KeyError: 'siret'` (ou `TypeError` sur LeadCandidate).

- [ ] **Step 3: Implement**

1. `models.py`, classe `Opportunity`, près de `siren`/`naf` :

```python
    siret: Optional[str] = None
    siren_match_method: Optional[str] = None      # nom | adresse | arbitre | source
    siren_match_confidence: Optional[str] = None  # haute | moyenne
```

2. `database.py`, dans le dict des migrations légères, ajouter :

```python
    "siret": "ALTER TABLE opportunities ADD COLUMN siret VARCHAR",
    "siren_match_method": "ALTER TABLE opportunities ADD COLUMN siren_match_method VARCHAR",
    "siren_match_confidence": "ALTER TABLE opportunities ADD COLUMN siren_match_confidence VARCHAR",
```

3. `base.py`, `LeadCandidate` : ajouter les 3 champs `siret: Optional[str] = None`, `siren_match_method: Optional[str] = None`, `siren_match_confidence: Optional[str] = None`.
4. `pipeline.py` :
   - `_match_lead` : renvoyer aussi `"siret": m.siret, "method": m.method, "confidence": m.confidence`.
   - `run_instagram` : `cand = LeadCandidate(..., siret=bf.get("siret"), siren_match_method=bf.get("method"), siren_match_confidence=bf.get("confidence"))`.
   - `_process_candidate` : persister les 3 champs à la création ET à la mise à jour (`existing.siret = cand.siret or existing.siret`, etc. — ne pas écraser par None).
5. `sirene_delta.map_etablissement` : poser `siret=etab.get("siret")` et `siren_match_method="source"` (le SIRET vient de la source elle-même, pas d'un matching).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/ -q` → tout vert (les tests existants `test_process_candidate_dedup_upsert` etc. ne doivent pas bouger).

- [ ] **Step 5: Commit**

```bash
git add backend/app/models.py backend/app/database.py backend/app/ingestion/base.py backend/app/ingestion/pipeline.py backend/app/ingestion/sirene_delta.py backend/tests/test_sirene_delta.py
git commit -m "feat(pipeline): persiste siret + method/confidence du matching (tracabilite + fusion)"
```

---

### Task 5: Fusion par SIREN (corroboration croisée registre × Instagram)

Cœur de la valeur de la brique 2 : un lead vu par DEUX sources = quasi-certain. Un candidat entrant dont le SIREN existe déjà sous une AUTRE source ne crée pas de doublon : il **fusionne** dans l'Opportunity existante (signal ajouté, provenances conservées, rescore).

**Files:**
- Modify: `backend/app/ingestion/pipeline.py` (`_process_candidate` : détection cross-source par SIREN avant l'upsert par source_ref)
- Test: `backend/tests/test_sirene_delta.py`

**Interfaces:**
- Produces: comportement — dans `_process_candidate`, si aucun existant `(source, source_ref)` mais qu'il existe une Opportunity avec le même `siren` (non nul) d'une autre source : fusion au lieu de création. Règles de fusion : champs contact/instagram remplis s'ils manquent ; `secondary_signals` += `"corroboré registre × instagram"` (une fois) ; `Signal` ajouté avec la source entrante ; `siret` posé s'il manquait ; rescore complet ; `stats.updated += 1`.

- [ ] **Step 1: Write the failing test**

```python
def test_fusion_par_siren_cross_source(tmp_path):
    """Un lead sirene entrant dont le SIREN existe deja cote instagram
    FUSIONNE (pas de doublon) : signal ajoute, corroboration, rescore."""
    from sqlmodel import SQLModel, Session, create_engine, select
    from app.models import Opportunity, Signal
    from app.ingestion.base import LeadCandidate
    from app.ingestion.pipeline import _process_candidate, IngestStats
    from datetime import date as _d

    engine = create_engine(f"sqlite:///{tmp_path/'t.db'}")
    SQLModel.metadata.create_all(engine)
    insta = LeadCandidate(
        source="instagram", source_ref="tregusto_sartrouville",
        establishment_name="Tre Gusto", city="Sartrouville",
        main_signal="ouverture prochaine", detection_date=_d(2026, 7, 5),
        classification_text="restaurant", establishment_type="restaurant",
        siren="989119201", instagram="tregusto_sartrouville",
    )
    sirene = LeadCandidate(
        source="sirene", source_ref="98911920100011",
        establishment_name="OCOIN", city="Sartrouville",
        main_signal="ouverture prochaine", detection_date=_d(2026, 7, 6),
        classification_text="restaurant", establishment_type="restaurant",
        siren="989119201", siret="98911920100011",
        address="143 AVENUE GENERAL DE GAULLE, 78500 Sartrouville",
    )
    stats = IngestStats(source="test")
    with Session(engine) as s:
        _process_candidate(s, insta, stats, set(), None)
        _process_candidate(s, sirene, stats, set(), None)
        s.commit()
        opps = s.exec(select(Opportunity)).all()
        assert len(opps) == 1  # fusion, pas de doublon
        opp = opps[0]
        assert opp.source == "instagram"          # la fiche d'origine est conservee
        assert opp.instagram == "tregusto_sartrouville"
        assert opp.siret == "98911920100011"      # complete par le registre
        assert opp.address                         # adresse registre posee
        assert "corroboré registre × instagram" in (opp.secondary_signals or [])
        signals = s.exec(select(Signal)).all()
        assert len(signals) == 2                   # 1 par provenance
        assert stats.created == 1 and stats.updated == 1


def test_pas_de_fusion_sans_siren(tmp_path):
    """Deux sources sans SIREN commun -> deux fiches (comportement inchange)."""
    from sqlmodel import SQLModel, Session, create_engine, select
    from app.models import Opportunity
    from app.ingestion.base import LeadCandidate
    from app.ingestion.pipeline import _process_candidate, IngestStats
    from datetime import date as _d

    engine = create_engine(f"sqlite:///{tmp_path/'t.db'}")
    SQLModel.metadata.create_all(engine)
    a = LeadCandidate(source="instagram", source_ref="h1", establishment_name="A",
                      city="Paris", main_signal="ouverture prochaine",
                      detection_date=_d(2026, 7, 6), classification_text="restaurant",
                      establishment_type="restaurant")
    b = LeadCandidate(source="sirene", source_ref="s1", establishment_name="B",
                      city="Paris", main_signal="ouverture prochaine",
                      detection_date=_d(2026, 7, 6), classification_text="restaurant",
                      establishment_type="restaurant", siren="111222333")
    with Session(engine) as s:
        st = IngestStats(source="test")
        _process_candidate(s, a, st, set(), None)
        _process_candidate(s, b, st, set(), None)
        s.commit()
        assert len(s.exec(select(Opportunity)).all()) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_sirene_delta.py -q`
Expected: FAIL — `assert len(opps) == 1` échoue (2 fiches créées).

- [ ] **Step 3: Implement**

Dans `_process_candidate` (pipeline.py), après le lookup `existing` par `(source, source_ref)` et AVANT la branche création, ajouter la détection cross-source :

```python
    # FUSION PAR SIREN [BRIQUE 2] : le meme etablissement vu par une AUTRE
    # source ne cree pas de doublon — il CORROBORE (registre x instagram =
    # quasi-certitude d'ouverture). La fiche d'origine est conservee, la
    # provenance entrante est journalisee en Signal.
    corroborated = None
    if existing is None and cand.siren:
        corroborated = session.exec(
            select(Opportunity).where(
                Opportunity.siren == cand.siren,
                Opportunity.source != cand.source,
            )
        ).first()
    if corroborated is not None:
        _merge_corroboration(session, corroborated, cand)
        stats.updated += 1
        return
```

Et le helper (même fichier) :

```python
CORROBORATION_TAG = "corroboré registre × instagram"


def _merge_corroboration(session, opp, cand) -> None:
    """Fusionne un candidat cross-source dans la fiche existante (ne remplit
    que les trous, n'ecrase rien), tague la corroboration et rescore."""
    opp.siret = opp.siret or cand.siret
    opp.address = opp.address or cand.address
    opp.email = opp.email or cand.email
    opp.website = opp.website or cand.website
    opp.instagram = opp.instagram or cand.instagram
    opp.naf = opp.naf or cand.naf
    opp.activity_start_date = opp.activity_start_date or cand.activity_start_date
    sigs = list(opp.secondary_signals or [])
    if CORROBORATION_TAG not in sigs:
        sigs.append(CORROBORATION_TAG)
    opp.secondary_signals = sigs
    channel = recommend_channel(
        establishment_type=opp.establishment_type,
        main_signal=opp.main_signal,
        secondary_signals=sigs,
        decision_maker=opp.decision_maker,
        has_social_presence=bool(opp.instagram),
    )
    score = compute_score(
        main_signal=opp.main_signal,
        secondary_signals=sigs,
        detection_date=opp.detection_date,
        probable_needs=opp.probable_needs,
        decision_maker=opp.decision_maker,
        recommended_channel=channel.channel,
        segment=classify_segment(opp.establishment_type, opp.naf, opp.establishment_name),
        review_count=opp.review_count,
    )
    opp.opportunity_score = score.score
    opp.score_reason = score.reason
    opp.recommended_channel = channel.channel
    opp.channel_reason = channel.reason
    opp.updated_at = datetime.utcnow()
    session.add(opp)
    session.add(Signal(
        opportunity_id=opp.id,
        signal_type=cand.main_signal,
        source={"bodacc": "BODACC", "instagram": "Instagram", "sirene": "Sirene (délta)"}.get(cand.source, cand.source),
        source_url=cand.proof_url,
        signal_date=cand.detection_date,
        confidence_score=0.9,
        raw_text=cand.proof_text,
    ))
```

Note : `select` est déjà importé dans pipeline.py ; vérifier que `Signal` l'est aussi (oui, ligne des imports models).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/ -q` → tout vert. Vérifier explicitement que `test_process_candidate_dedup_upsert` (dédup même-source existante) passe toujours : la fusion ne s'applique QUE cross-source (`Opportunity.source != cand.source`).

- [ ] **Step 5: Commit**

```bash
git add backend/app/ingestion/pipeline.py backend/tests/test_sirene_delta.py
git commit -m "feat(pipeline): fusion par SIREN cross-source (corroboration registre x instagram)"
```

---

### Task 6: Gate live borné + documentation

**Files:**
- Modify: `chr-signal-radar/CLAUDE.md` (env `INSEE_API_KEY`, source `sirene` dans les modes CLI)
- Modify: `chr-signal-radar/docs/ARCHITECTURE.md` (§9 : brique 2 → Fait ; §3 : connecteur sirene dans le tableau des sources)

**Interfaces:** aucune (validation + docs).

- [ ] **Step 1: Smoke test live borné (la clé est dans backend/.env)**

Depuis `backend/` (charger l'env : la CLI le fait via dotenv ; sinon `set -a; . .env` en bash) :

```
.venv\Scripts\python.exe -m app.ingestion.run --mode window --source sirene --since 3 --limit 300 --departments 75 92 93 94
```

Attendu : stats non vides (`fetched > 0`, `created > 0`), zéro `errors`, durée < 2 min. Vérifier en base (ou via l'UI) que les leads `source="sirene"` ont : nom exploitable (pas de `[ND]`), `siret` rempli, `main_signal="ouverture prochaine"`, un score, et que les éventuels pré-déclarés portent la mention dans `proof_text`. Si `fetched == 0` : vérifier la clé et rejouer avec `--since 7` sans departments.

- [ ] **Step 2: Non-régression complète**

```
.venv\Scripts\python.exe -m pytest tests/ -q
.venv\Scripts\python.exe -m app.ingestion.eval.match_eval
```

Attendu : suite verte ; éval matching inchangée (8/9, 0 faux merge, exit 0).

- [ ] **Step 3: Documentation**

Dans `CLAUDE.md` : ajouter `INSEE_API_KEY=` à la liste des variables `.env` backend, et mentionner la source `sirene` dans la section ETL (délta des nouveaux SIRET, fenêtre passée + futur pré-déclaré).
Dans `docs/ARCHITECTURE.md` : §9 tableau — brique 2 passe à « **Fait** (2026-07-06) » ; §3 : ajouter le connecteur `sirene_delta.py` (rôle, fenêtre, fusion par SIREN) à côté de BODACC ; §1 schéma : ajouter « Sirene délta (INSEE) » dans la colonne SOURCES.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md docs/ARCHITECTURE.md
git commit -m "docs: brique 2 (delta-Sirene) — CLI, env INSEE_API_KEY, architecture a jour"
```

---

## Hors périmètre (briques/plans suivants)

Règle déterministe « succession au même numéro → le plus récent » dans `pick_by_address` (l'arbitre la gère avec contexte, éval verte) ; télémétrie/audit hebdo des matchs ; brique 3 (funnel v2 + cache `handle_verdicts`, y c. fix d'ancrage de date de `_judge_profile`) ; brique 4 (watchlist/réconciliation) ; scheduling quotidien du délta (roadmap MVP §3 hébergement/scheduling).
