# Inventaire complet + précision (brique 3bis) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL — utiliser `superpowers:subagent-driven-development` (recommandé) ou `superpowers:executing-plans` pour exécuter ce plan tâche-par-tâche. Étapes en `- [ ]` pour le suivi. Chaque tâche porte un **Modèle d'exécution recommandé**.

**Goal:** Faire de l'app un **inventaire complet** du CHR d'Île-de-France, pas seulement un radar d'ouvertures. Le funnel Insta v2 (brique 3, mergée) étiquette déjà chaque compte d'un label de cycle de vie ; il ne créait un lead que pour `opening_soon`/`just_opened`/`unknown` (avec un `unknown` **déguisé** en « ouverture prochaine », source de faux positifs). Brique 3bis :

1. **PERSISTE** le label sur la fiche (`lifecycle_label`), l'expose à l'API + un filtre.
2. **ROUTE tout label en lead** sauf `not_venue`/`noise` : les établis, chaînes et indéterminés entrent **en base** avec un signal NEUTRE (score naturellement bas — les ouvertures restent en tête du tri), sans jamais inventer de signal d'achat. `unknown` n'est **plus** déguisé en « ouverture prochaine ».
3. **AFFINE** garde-fous & juge (mesuré sur l'éval, 20 snapshots) : plus d'établis captés par les gardes (résa dans la bio/les posts), chaînes sous le seuil 150 posts détectées.
4. **Éval v2bis** : buckets révisés (`en_base`), nouvelle métrique honnête (précision du segment chaud ≥ 60 %), rappel opening 4/4 inchangé.

**Décision produit d'Alexis (2026-07-06, encodée telle quelle) :**
> « Tout peut être lead ; les ouvertures sont un type spécial et privilégié. Les autres (établis, chaînes) restent en base. Une chaîne de petits shops qui s'étend veut ouvrir de nouveaux lieux : c'est un lead, pas un rejet. »

**Architecture (delta vs brique 3) :** `run_instagram` inchangé jusqu'au label (`discover → should_rejudge → scrape_profiles → classify_profiles`). Le **routage** change : `LABEL_ROUTING` mappe chaque label vers `(main_signal, secondary_signals, lifecycle_label)` ; `not_venue`/`noise` restent hors routage (cache seul). `classify_profiles`/`guard_verdict`/`judge_dossier` gagnent en précision (nouveaux gardes déterministes + prompt chaîne). Le cache `HandleVerdict` et ses fenêtres sont **inchangés** (un établi n'est pas re-jugé pendant 6 mois).

**Tech Stack:** Python 3.9 (`Optional[X]`/`Dict`/`List` de `typing`, jamais `X | None`), SQLModel/SQLite (migration légère par `ALTER TABLE ADD COLUMN` dans `database.py`), OpenAI (optionnel, fail-soft), pytest. Docstrings/commentaires/prompts **en français**.

## Global Constraints

- **Python 3.9** ; **fail-soft partout** (pas de clé/erreur LLM → `unknown` = doute → gardé ; pas de token Apify → scrape `{}`). **Aucun appel réseau/LLM réel dans les tests unitaires** : clients/`match_fn`/`scrape_profiles` injectés (pattern `_FakeClient`). Le seul LLM live autorisé est l'éval de classification (gate final, T4).
- **Répertoires** : `python`/`pytest` depuis `chr-signal-radar/backend` avec `.venv\Scripts\python.exe` ; `git` depuis la racine `chr-signal-radar/`. Branche **`feature/inventaire-complet`**. **Pas de push, pas de `--no-verify`.**
- `python -m pytest tests/ -q` **vert à la fin de CHAQUE tâche**. **Éval de matching** (`app.ingestion.eval.match_eval`, offline) **inchangée : 8/9, 0 faux merge**.
- **TDD strict** : tests d'abord (RED), puis implémentation (GREEN), puis commit avec le message exact fourni.
- **Créer la branche avant la Task 1** (depuis la racine) :

```bash
git checkout -b feature/inventaire-complet
```

**Labels de cycle de vie** (espace de sortie du funnel, inchangé) :
`opening_soon | just_opened | established | chain_multisite | not_venue | noise | unknown`.

**Routage label → lead (cible brique 3bis, encodé en `LABEL_ROUTING`) :**

| label | lead ? | `main_signal` | `secondary_signals` | `lifecycle_label` |
|---|---|---|---|---|
| `opening_soon` | oui (chaud) | `ouverture prochaine` | — | `opening_soon` |
| `just_opened` | oui (chaud) | `création récente` | — | `just_opened` |
| `established` | **oui (en base)** | `établissement en activité` *(neutre)* | — | `established` |
| `chain_multisite` | **oui (en base)** | `établissement en activité` *(neutre)* | `extension multi-sites` | `chain_multisite` |
| `unknown` | oui (en base) | `établissement en activité` *(neutre)* | — | `unknown` |
| `not_venue` | **non** (cache seul) | — | — | — |
| `noise` | **non** (cache seul) | — | — | — |

Le signal neutre `établissement en activité` est ajouté à `SIGNAL_TYPES` ; il n'est membre d'**aucune** famille de scoring (`OPENING_SIGNALS`/`TAKEOVER`/`RENOVATION`/`RECRUITMENT` de `services/scoring.py`) → **aucun bonus de nature** → score naturellement bas. Le libellé secondaire `extension multi-sites` est **exactement** celui déjà posé par le delta-Sirene (`sirene_delta.py:89`) — harmonisation voulue.

---

### Task 1: Colonne `lifecycle_label` — persistance + exposition API + filtre

**Modèle d'exécution recommandé : sonnet**

**Files:**
- Modify: `backend/app/models.py` (champ `lifecycle_label` sur `Opportunity` + entrée `SIGNAL_TYPES`)
- Modify: `backend/app/database.py` (migration légère : `ADD COLUMN lifecycle_label`)
- Modify: `backend/app/ingestion/base.py` (champ `lifecycle_label` sur `LeadCandidate`)
- Modify: `backend/app/ingestion/pipeline.py` (`_process_candidate` create + update, `_merge_corroboration` : persister `lifecycle_label`)
- Modify: `backend/app/schemas.py` (`OpportunityList.lifecycle_label`)
- Modify: `backend/app/routes/opportunities.py` (paramètre + filtre `lifecycle_label`)
- Create: `backend/tests/test_lifecycle_label.py`

**Interfaces:**
- `Opportunity.lifecycle_label: Optional[str] = None` (indexé — pour le filtre ; NULL pour BODACC/Sirene qui n'étiquettent pas encore). Ajouté à `SIGNAL_TYPES` : `"établissement en activité"` (neutre).
- `LeadCandidate.lifecycle_label: Optional[str] = None` — porté de bout en bout par le pipeline.
- `_process_candidate` : `opp.lifecycle_label = cand.lifecycle_label` à la création ; `existing.lifecycle_label = cand.lifecycle_label or existing.lifecycle_label` à l'upsert même-source. `_merge_corroboration` : `opp.lifecycle_label = opp.lifecycle_label or cand.lifecycle_label` (ne remplit que le trou).
- `GET /api/opportunities?lifecycle_label=established` → filtre `Opportunity.lifecycle_label == …`.
- **Cette tâche n'affecte PAS le routage** (T2) : `run_instagram` ne renseigne pas encore `lifecycle_label`, donc les leads Insta restent inchangés ; la colonne existe et est filtrable, à `None` partout pour l'instant. Migration idempotente (les autres colonnes indexées — `source_ref`, `siren` — sont déjà migrées par un `ADD COLUMN` simple sans index ; on suit la même convention, l'index n'existe que pour les bases neuves via `create_all`, ce qui est acceptable).

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_lifecycle_label.py
"""Tests de la colonne lifecycle_label (brique 3bis, T1) : migration, persistance,
exposition API + filtre. Aucun réseau."""
from datetime import date

from sqlmodel import Session, SQLModel, create_engine, select

from app.ingestion.base import LeadCandidate
from app.ingestion.pipeline import IngestStats, _process_candidate
from app.models import Opportunity
from app.routes.opportunities import list_opportunities


def _engine():
    e = create_engine("sqlite://")
    SQLModel.metadata.create_all(e)
    return e


def test_signal_types_contains_neutral():
    from app.models import SIGNAL_TYPES
    assert "établissement en activité" in SIGNAL_TYPES


def test_leadcandidate_has_lifecycle_label():
    c = LeadCandidate(source="instagram", source_ref="x", establishment_name="X",
                      city="Paris", main_signal="ouverture prochaine",
                      detection_date=date(2026, 7, 6), establishment_type="restaurant",
                      lifecycle_label="opening_soon")
    assert c.lifecycle_label == "opening_soon"


def test_process_candidate_persists_lifecycle_label():
    with Session(_engine()) as s:
        cand = LeadCandidate(
            source="instagram", source_ref="etabli1", establishment_name="Vieux Bistrot",
            city="Paris", address="", main_signal="établissement en activité",
            detection_date=date(2026, 7, 6), establishment_type="restaurant",
            lifecycle_label="established",
        )
        _process_candidate(s, cand, IngestStats(source="instagram"), set(), enricher=None)
        s.commit()
        opp = s.exec(select(Opportunity).where(Opportunity.source_ref == "etabli1")).first()
        assert opp is not None and opp.lifecycle_label == "established"


def test_process_candidate_update_refreshes_lifecycle_label():
    with Session(_engine()) as s:
        base = dict(source="instagram", source_ref="h1", establishment_name="H",
                    city="Paris", address="", detection_date=date(2026, 7, 6),
                    establishment_type="restaurant")
        _process_candidate(s, LeadCandidate(main_signal="établissement en activité",
                                            lifecycle_label="unknown", **base),
                           IngestStats(source="instagram"), set(), enricher=None)
        s.commit()
        _process_candidate(s, LeadCandidate(main_signal="ouverture prochaine",
                                            lifecycle_label="opening_soon", **base),
                           IngestStats(source="instagram"), set(), enricher=None)
        s.commit()
        opp = s.exec(select(Opportunity).where(Opportunity.source_ref == "h1")).first()
        assert opp.lifecycle_label == "opening_soon"  # rafraîchi à l'upsert même-source


def test_api_filters_by_lifecycle_label():
    with Session(_engine()) as s:
        for ref, lab, sig in [("a", "established", "établissement en activité"),
                              ("b", "opening_soon", "ouverture prochaine")]:
            _process_candidate(
                s, LeadCandidate(source="instagram", source_ref=ref, establishment_name=ref,
                                 city="Paris", address="", main_signal=sig,
                                 detection_date=date(2026, 7, 6), establishment_type="restaurant",
                                 lifecycle_label=lab),
                IngestStats(source="instagram"), set(), enricher=None)
        s.commit()
        got = list_opportunities(session=s, lifecycle_label="established")
        assert [o.source_ref for o in got] == ["a"]


def test_migration_adds_column_on_existing_db(tmp_path):
    from sqlalchemy import create_engine as ce, inspect, text
    import app.database as db
    url = f"sqlite:///{tmp_path/'legacy.db'}"
    # Base « ancienne » sans la colonne.
    old = ce(url)
    with old.begin() as conn:
        conn.execute(text("CREATE TABLE opportunities (id INTEGER PRIMARY KEY, "
                          "establishment_name VARCHAR, establishment_type VARCHAR, "
                          "city VARCHAR, address VARCHAR, main_signal VARCHAR, "
                          "detection_date DATE, estimated_timing VARCHAR)"))
    old.dispose()
    # Repointer le moteur du module vers cette base, puis migrer.
    orig_engine, orig_url = db.engine, db.DATABASE_URL
    db.engine, db.DATABASE_URL = ce(url), url
    try:
        db._run_lightweight_migrations()
        cols = {c["name"] for c in inspect(db.engine).get_columns("opportunities")}
        assert "lifecycle_label" in cols
    finally:
        db.engine.dispose()
        db.engine, db.DATABASE_URL = orig_engine, orig_url
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_lifecycle_label.py -q`
Expected: FAIL — `TypeError` (`LeadCandidate` sans `lifecycle_label`), `AttributeError`/`AssertionError` (colonne/param absents).

- [ ] **Step 3: Write the implementation**

**a) `models.py`** — ajouter la valeur neutre à `SIGNAL_TYPES` (après `"expansion"`) :

```python
SIGNAL_TYPES = [
    "ouverture prochaine",
    "reprise",
    "rénovation",
    "recrutement",
    "changement propriétaire",
    "nouveau point de vente",
    "travaux visibles",
    "annonce presse locale",
    "création récente",
    "expansion",
    # Signal NEUTRE des leads « en base » (établis/chaînes/indéterminés du funnel
    # Insta) : membre d'AUCUNE famille de scoring -> aucun bonus de nature.
    "établissement en activité",
]
```

Et, dans la classe `Opportunity`, ajouter le champ juste après `siren_match_confidence` :

```python
    siren_match_method: Optional[str] = None      # nom | adresse | arbitre | source
    siren_match_confidence: Optional[str] = None  # haute | moyenne

    # Étiquette de cycle de vie du funnel Insta (juge/gardes) PERSISTÉE sur la
    # fiche : opening_soon | just_opened | established | chain_multisite | unknown.
    # NULL pour les sources registre (BODACC/Sirene) qui n'étiquettent pas encore.
    lifecycle_label: Optional[str] = Field(default=None, index=True)
```

**b) `database.py`** — ajouter l'entrée dans le dict `additions` de `_run_lightweight_migrations` (à la suite, ex. après `extra_emails`) :

```python
        "extra_addresses": "ALTER TABLE opportunities ADD COLUMN extra_addresses JSON",
        "extra_emails": "ALTER TABLE opportunities ADD COLUMN extra_emails JSON",
        "lifecycle_label": "ALTER TABLE opportunities ADD COLUMN lifecycle_label VARCHAR",
    }
```

**c) `base.py`** — ajouter le champ à `LeadCandidate`, juste après `secondary_signals` :

```python
    secondary_signals: List[str] = field(default_factory=list)
    # Label de cycle de vie du funnel Insta (persisté sur la fiche). None pour les
    # sources registre (BODACC/Sirene).
    lifecycle_label: Optional[str] = None
    decision_maker: Optional[str] = None
```

**d) `pipeline.py`** :

Dans `_process_candidate`, branche **création** (`opp = Opportunity(...)`), ajouter la ligne (ex. juste après `instagram=cand.instagram,`) :

```python
        instagram=cand.instagram,
        lifecycle_label=cand.lifecycle_label,
        email=cand.email,
```

Dans la branche **upsert même-source** (`if existing:`), ajouter après `existing.instagram = cand.instagram` (dans le bloc `if cand.instagram:`) — mais **inconditionnellement**, juste avant `existing.activity_start_date = ...` :

```python
        # Rafraîchir le label de cycle de vie (un opening peut devenir established
        # à un run ultérieur, ou l'inverse) — ne pas écraser par None (BODACC).
        existing.lifecycle_label = cand.lifecycle_label or existing.lifecycle_label
        existing.activity_start_date = cand.activity_start_date
```

Dans `_merge_corroboration`, ajouter après `opp.naf = opp.naf or cand.naf` :

```python
        opp.naf = opp.naf or cand.naf
        opp.lifecycle_label = opp.lifecycle_label or cand.lifecycle_label
```

**e) `schemas.py`** — ajouter à `OpportunityList` (ex. après `source_ref`) :

```python
    source_ref: Optional[str] = None
    lifecycle_label: Optional[str] = None
    siren: Optional[str] = None
```

**f) `routes/opportunities.py`** — ajouter le paramètre et le filtre dans `list_opportunities` :

```python
    source: Optional[str] = None,
    lifecycle_label: Optional[str] = None,
    sort_by: str = "score",
```

et, dans le corps (après le bloc `if source:`) :

```python
    if source:
        query = query.where(Opportunity.source == source)
    if lifecycle_label:
        query = query.where(Opportunity.lifecycle_label == lifecycle_label)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_lifecycle_label.py -q` → PASS (7 tests).
Run: `python -m pytest tests/ -q` → tout vert (la colonne est neuve, `None` partout, aucun test existant ne la lit).

- [ ] **Step 5: Commit**

```bash
git add backend/app/models.py backend/app/database.py backend/app/ingestion/base.py backend/app/ingestion/pipeline.py backend/app/schemas.py backend/app/routes/opportunities.py backend/tests/test_lifecycle_label.py
git commit -m "feat(inventaire): colonne lifecycle_label persistee (Opportunity, migration, LeadCandidate, API filtre)"
```

---

### Task 2: Routage des labels en leads (`LABEL_ROUTING`) — établis/chaînes/unknown en base

**Modèle d'exécution recommandé : opus**

**Files:**
- Modify: `backend/app/ingestion/pipeline.py` (constantes `NEUTRAL_SIGNAL`/`MULTISITE_SIGNAL`/`LABEL_ROUTING` ; boucle de routage dans `run_instagram`)
- Modify: `backend/tests/test_funnel_v2.py` (mise à jour des assertions devenues fausses : MOKA/chaîne devient un lead ; `unknown` a un signal neutre)
- Create: `backend/tests/test_inventaire_routing.py`

**Interfaces:**
- `pipeline.LABEL_ROUTING: Dict[str, Tuple[str, List[str], str]]` — `{label: (main_signal, secondary_signals, lifecycle_label)}`. `not_venue`/`noise` **absents** → pas de lead. Voir la table du préambule.
- `run_instagram` : la boucle sur `labeled` conserve le cache **à l'identique** (mêmes conditions `cacheable`, mêmes fenêtres), mais la **création de lead** est pilotée par `LABEL_ROUTING` : tout label routé produit un `LeadCandidate` avec `main_signal`/`secondary_signals`/`lifecycle_label` mappés. Les établis/chaînes sont désormais **aussi** cachés (déjà le cas — leur verdict était caché) ET créés en lead (nouveau).
- **Score naturellement bas** garanti par construction : `NEUTRAL_SIGNAL` hors familles de scoring → `compute_score` ne pose aucun bonus de nature ; un chain gagne au plus +1 « signaux croisés » (2 familles distinctes : neutre + `extension multi-sites`), très en-dessous du +3 « ouverture » des chauds. Aucune modification de `services/scoring.py` (contrat des autres sources préservé).

- [ ] **Step 1: Write the failing tests**

Créer `backend/tests/test_inventaire_routing.py` :

```python
# backend/tests/test_inventaire_routing.py
"""Routage des labels en leads (brique 3bis, T2) — sans réseau ni LLM réels."""
import json
from datetime import date
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine, select

import app.ingestion.instagram as ig
import app.ingestion.pipeline as pl
from app.models import HandleVerdict, Opportunity

SNAP = Path(__file__).resolve().parents[1] / "app" / "ingestion" / "eval" / "snapshots"


class _FakeCompletion:
    def __init__(self, content):
        self.choices = [type("C", (), {"message": type("M", (), {"content": content})()})()]


class _FakeClient:
    def __init__(self, content):
        class _Completions:
            def create(_self, **kwargs):
                return _FakeCompletion(content)
        self.chat = type("Chat", (), {"completions": _Completions()})()


def _no_enricher():
    class _NoEnricher:
        def enrich(self, cand):
            return None

        def lookup(self, siren):
            return None
    return _NoEnricher()


def _prep(monkeypatch, profiles, judge_json=None):
    monkeypatch.setattr(pl, "scrape_profiles", lambda handles, **k: profiles)
    monkeypatch.setattr(pl, "match_siret", lambda **kw: None)
    monkeypatch.setattr(pl, "SireneEnricher", lambda: _no_enricher())
    if judge_json is None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    else:
        monkeypatch.setenv("OPENAI_API_KEY", "x")
        monkeypatch.setattr(ig, "_openai_client", lambda: _FakeClient(judge_json))


def _post(handle, caption="ouverture prochaine à Paris", hashtags=("ouvertureprochaine",)):
    return {"ownerUsername": handle, "ownerFullName": handle, "caption": caption,
            "hashtags": list(hashtags), "locationName": "Paris"}


def _run(engine, posts):
    with Session(engine) as s:
        stats = pl.run_instagram(posts=posts, session=s)
        s.commit()
        opps = {o.source_ref: o for o in s.exec(select(Opportunity)).all()}
        verdicts = {v.handle: v.verdict for v in s.exec(select(HandleVerdict)).all()}
        return stats, opps, verdicts


def _engine(tmp_path):
    e = create_engine(f"sqlite:///{tmp_path/'t.db'}")
    SQLModel.metadata.create_all(e)
    return e


def test_established_becomes_low_score_lead(tmp_path, monkeypatch):
    # postsCount > 150 -> garde-fou established (pas de LLM).
    prof = {"postsCount": 200, "biography": "Bistrot de quartier",
            "latestPosts": [{"timestamp": "2026-07-01T10:00:00.000Z"}]}
    _prep(monkeypatch, {"vieuxbistrot": prof})
    _, opps, verdicts = _run(_engine(tmp_path), [_post("vieuxbistrot", "resto à Paris", ())])
    opp = opps["vieuxbistrot"]
    assert opp.lifecycle_label == "established"
    assert opp.main_signal == "établissement en activité"
    assert not (set(opp.secondary_signals or []))  # aucun signal secondaire
    assert opp.opportunity_score <= 5               # naturellement bas (aucun bonus d'ouverture)
    assert verdicts["vieuxbistrot"] == "established"  # caché comme avant


def test_chain_lead_has_multisite_secondary(tmp_path, monkeypatch):
    moka = json.loads((SNAP / "cafe_mokaparis.json").read_text(encoding="utf-8"))
    _prep(monkeypatch, {"cafe_mokaparis": moka})
    _, opps, verdicts = _run(_engine(tmp_path), [_post("cafe_mokaparis", "café à Paris", ("cafeparis",))])
    opp = opps["cafe_mokaparis"]
    assert opp.lifecycle_label == "chain_multisite"
    assert "extension multi-sites" in (opp.secondary_signals or [])
    assert opp.main_signal == "établissement en activité"
    assert verdicts["cafe_mokaparis"] == "chain_multisite"


def test_unknown_lead_is_neutral_not_disguised(tmp_path, monkeypatch):
    prof = {"postsCount": 2, "biography": "Ouverture prochaine",
            "latestPosts": [{"timestamp": "2026-06-20T10:00:00.000Z"}]}
    _prep(monkeypatch, {"douteux": prof})  # pas de juge -> unknown
    _, opps, _ = _run(_engine(tmp_path), [_post("douteux")])
    opp = opps["douteux"]
    assert opp.lifecycle_label == "unknown"
    # Plus de faux « ouverture prochaine » : signal neutre.
    assert opp.main_signal == "établissement en activité"


def test_not_venue_and_noise_no_lead_but_cached(tmp_path, monkeypatch):
    prof = {"postsCount": 3, "biography": "Marque de bijoux",
            "latestPosts": [{"timestamp": "2026-06-20T10:00:00.000Z", "caption": "collection"}]}
    for label in ("not_venue", "noise"):
        _prep(monkeypatch, {"marque": prof},
              judge_json=('{"reasoning":"x","label":"%s","confidence":"haute",'
                          '"addresses":[],"emails":[],"opening_date":null}' % label))
        _, opps, verdicts = _run(_engine(tmp_path), [_post("marque", "bijoux", ())])
        assert "marque" not in opps               # pas de lead
        assert verdicts.get("marque") == label     # mais verdict caché


def test_opening_still_hot(tmp_path, monkeypatch):
    prof = {"postsCount": 2, "biography": "on ouvre bientôt",
            "latestPosts": [{"timestamp": "2026-06-20T10:00:00.000Z", "caption": "travaux"}]}
    _prep(monkeypatch, {"loumas": prof},
          judge_json='{"reasoning":"x","label":"opening_soon","confidence":"haute",'
                     '"addresses":[],"emails":[],"opening_date":null}')
    _, opps, _ = _run(_engine(tmp_path), [_post("loumas")])
    opp = opps["loumas"]
    assert opp.lifecycle_label == "opening_soon"
    assert opp.main_signal == "ouverture prochaine"


def test_opening_outranks_established(tmp_path, monkeypatch):
    profs = {
        "vieux": {"postsCount": 200, "biography": "Bistrot",
                  "latestPosts": [{"timestamp": "2026-07-01T10:00:00.000Z"}]},
        "neuf": {"postsCount": 2, "biography": "on ouvre bientôt",
                 "latestPosts": [{"timestamp": "2026-06-20T10:00:00.000Z", "caption": "travaux"}]},
    }
    _prep(monkeypatch, profs,
          judge_json='{"reasoning":"x","label":"opening_soon","confidence":"haute",'
                     '"addresses":[],"emails":[],"opening_date":null}')
    _, opps, _ = _run(_engine(tmp_path),
                      [_post("neuf"), _post("vieux", "resto à Paris", ())])
    assert opps["neuf"].opportunity_score > opps["vieux"].opportunity_score
```

Mettre à jour `backend/tests/test_funnel_v2.py` — `test_run_instagram_labels_leads_and_cache` : MOKA devient **un lead** (chaîne). Remplacer le corps du `with Session(...)` et la docstring :

```python
def test_run_instagram_labels_leads_and_cache(tmp_path, monkeypatch):
    """MOKA -> chain_multisite : DÉSORMAIS un lead « en base » (extension
    multi-sites) ET un verdict caché. newresto -> unknown fail-soft (lead créé,
    signal neutre, NON caché : sans juge, confiance 'basse' -> reste dû)."""
    engine = create_engine(f"sqlite:///{tmp_path/'t.db'}")
    SQLModel.metadata.create_all(engine)
    moka = json.loads((SNAP / "cafe_mokaparis.json").read_text(encoding="utf-8"))
    profiles = {
        "cafe_mokaparis": moka,
        "newresto": {"postsCount": 2, "biography": "Ouverture prochaine",
                     "latestPosts": [{"timestamp": "2026-06-20T10:00:00.000Z"}]},
    }
    monkeypatch.setattr(pl, "scrape_profiles", lambda handles, **k: profiles)
    monkeypatch.setattr(pl, "match_siret", lambda **kw: None)
    monkeypatch.setattr(pl, "SireneEnricher", lambda: _no_enricher())
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    posts = [
        {"ownerUsername": "newresto", "ownerFullName": "Le Nouveau Resto",
         "caption": "Ouverture prochaine à Paris", "hashtags": ["ouvertureprochaine"],
         "locationName": "Paris, France"},
        {"ownerUsername": "cafe_mokaparis", "ownerFullName": "MOKA",
         "caption": "café à Paris", "hashtags": ["cafeparis"], "locationName": "Paris"},
    ]
    with Session(engine) as s:
        stats = pl.run_instagram(posts=posts, session=s)
        s.commit()
        opps = {o.source_ref: o for o in s.exec(select(Opportunity)).all()}
        assert "newresto" in opps               # unknown -> lead
        assert "cafe_mokaparis" in opps          # chain_multisite -> lead (en base)
        assert opps["cafe_mokaparis"].lifecycle_label == "chain_multisite"
        assert "extension multi-sites" in (opps["cafe_mokaparis"].secondary_signals or [])
        assert opps["newresto"].main_signal == "établissement en activité"  # plus déguisé
        verdicts = {v.handle: v.verdict for v in s.exec(select(HandleVerdict)).all()}
        assert verdicts["cafe_mokaparis"] == "chain_multisite"
        assert "newresto" not in verdicts  # unknown 'basse' non caché
    assert stats.errors == 0
```

(Les autres tests de `test_funnel_v2.py` restent inchangés : `test_run_instagram_verdict_survives_lead_failure` continue de passer — MOKA crée maintenant un lead **avant** l'échec de `boomresto`, mais ses assertions ne portent que sur la survie du verdict et `stats.errors == 1`. Sa docstring dit « pas de lead » : la corriger en « chain_multisite (garde) -> verdict caché **et lead en base** » pour rester honnête.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_inventaire_routing.py tests/test_funnel_v2.py -q`
Expected: FAIL — `LABEL_ROUTING` absent (AttributeError) ; les nouvelles assertions (`cafe_mokaparis in opps`, `main_signal == "établissement en activité"`) tombent contre le routage actuel (MOKA écarté, unknown → « ouverture prochaine »).

- [ ] **Step 3: Write the implementation**

Dans `backend/app/ingestion/pipeline.py`, ajouter les constantes (près de `TIMING_BY_SIGNAL`, avant `CONNECTORS`) :

```python
# Signal NEUTRE des leads « en base » (établis, chaînes, indéterminés du funnel
# Insta) : présent dans SIGNAL_TYPES, membre d'AUCUNE famille de scoring
# (services/scoring.py) -> aucun bonus de nature, score naturellement bas (les
# ouvertures restent en tête du tri). On n'invente AUCUN signal d'achat pour un
# établissement qui n'en émet pas.
NEUTRAL_SIGNAL = "établissement en activité"
# Libellé harmonisé avec le delta-Sirene (sirene_delta.py) : une marque qui ouvre
# un nouveau lieu = extension multi-sites (signal secondaire, non chaud).
MULTISITE_SIGNAL = "extension multi-sites"

# Routage label de cycle de vie -> (main_signal, secondary_signals, lifecycle_label).
# not_venue/noise ABSENTS -> aucun lead (verdict caché uniquement). unknown =
# lead « en base » NEUTRE (plus jamais déguisé en « ouverture prochaine »).
LABEL_ROUTING = {
    "opening_soon":    ("ouverture prochaine", [],                 "opening_soon"),
    "just_opened":     ("création récente",    [],                 "just_opened"),
    "established":     (NEUTRAL_SIGNAL,         [],                 "established"),
    "chain_multisite": (NEUTRAL_SIGNAL,         [MULTISITE_SIGNAL], "chain_multisite"),
    "unknown":         (NEUTRAL_SIGNAL,         [],                 "unknown"),
}
```

Dans `run_instagram`, remplacer le bloc de création de lead. Aujourd'hui :

```python
            # Création de lead UNIQUEMENT pour opening_soon/just_opened/unknown
            # (unknown = doute -> garde, protège le recall).
            if c["label"] not in ("opening_soon", "just_opened", "unknown"):
                continue
            try:
                main_signal = {
                    "opening_soon": "ouverture prochaine",
                    "just_opened": "création récente",
                    "unknown": "ouverture prochaine",
                }[c["label"]]
                m = c.get("_match")
                cand = LeadCandidate(
                    source="instagram",
                    source_ref=c["handle"],
                    establishment_name=(m.enseigne if (m and m.enseigne) else c["name"]),
                    city=c["city"],
                    address=c.get("address", ""),
                    email=c.get("email"),
                    website=c.get("website"),
                    extra_addresses=c.get("extra_addresses", []),
                    extra_emails=c.get("extra_emails", []),
                    main_signal=main_signal,
                    detection_date=today,
                    classification_text=c["name"],
                    establishment_type=c["type"],  # pré-classé CHR à la découverte
                    instagram=c["handle"],
                    siren=(m.siren if m else None),
                    naf=(m.naf if m else None),
                    siret=(m.siret if m else None),
                    siren_match_method=(m.method if m else None),
                    siren_match_confidence=(m.confidence if m else None),
                )
```

Remplacer par :

```python
            # ROUTAGE brique 3bis : TOUT label devient un lead SAUF not_venue/noise
            # (absents de LABEL_ROUTING -> cache seul). Les ouvertures gardent leur
            # signal d'achat ; établis/chaînes/unknown reçoivent un signal NEUTRE
            # (score naturellement bas) + le label de cycle de vie persisté.
            routing = LABEL_ROUTING.get(c["label"])
            if routing is None:
                continue
            main_signal, secondary_signals, lifecycle_label = routing
            try:
                m = c.get("_match")
                cand = LeadCandidate(
                    source="instagram",
                    source_ref=c["handle"],
                    establishment_name=(m.enseigne if (m and m.enseigne) else c["name"]),
                    city=c["city"],
                    address=c.get("address", ""),
                    email=c.get("email"),
                    website=c.get("website"),
                    extra_addresses=c.get("extra_addresses", []),
                    extra_emails=c.get("extra_emails", []),
                    main_signal=main_signal,
                    secondary_signals=list(secondary_signals),
                    lifecycle_label=lifecycle_label,
                    detection_date=today,
                    classification_text=c["name"],
                    establishment_type=c["type"],  # pré-classé CHR à la découverte
                    instagram=c["handle"],
                    siren=(m.siren if m else None),
                    naf=(m.naf if m else None),
                    siret=(m.siret if m else None),
                    siren_match_method=(m.method if m else None),
                    siren_match_confidence=(m.confidence if m else None),
                )
```

(Le reste de la boucle — `_process_candidate(...)`, `session.commit()`, `except` — est **inchangé**. Le bloc cache au-dessus — `cacheable`/`verdict_cache.upsert` — est **inchangé** : établis/chaînes restent cachés 6 mois, `unknown`/`noise` 2 mois.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_inventaire_routing.py tests/test_funnel_v2.py -q` → PASS.
Run: `python -m pytest tests/ -q` → tout vert.

- [ ] **Step 5: Commit**

```bash
git add backend/app/ingestion/pipeline.py backend/tests/test_inventaire_routing.py backend/tests/test_funnel_v2.py
git commit -m "feat(inventaire): routage des labels en leads (etabli/chaine/unknown en base, plus de faux 'ouverture prochaine')"
```

---

### Task 3: Précision des garde-fous & du juge (résa bio+posts, multi-villes, `newtable`, prompt chaîne)

**Modèle d'exécution recommandé : opus**

**Files:**
- Modify: `backend/app/ingestion/profile_guards.py` (helpers `_has_reservation_in_bio`, `_has_reservation_in_posts`, `_multi_city_in_bio` ; `_RESA_HOSTS` += `newtable` ; `guard_verdict` élargi)
- Modify: `backend/app/ingestion/instagram.py` (règle chaîne « 2e adresse » dans `_DOSSIER_SYSTEM`)
- Modify: `backend/tests/test_profile_guards.py` (snapshots osabaita/villa/cheres + helpers)
- Modify: `backend/tests/test_judge_dossier.py` (prompt mentionne la 2e adresse)

**Interfaces:**
- `_has_reservation_in_bio(bio) -> bool` : bio contenant un mot-clé de réservation (`réserv…`) **et** un numéro de téléphone français → établissement en service. Capte **osabaita** (bio `Réservation : 01 43 25 87 99`).
- `_has_reservation_in_posts(profile) -> bool` : un des ~12 derniers posts a une légende avec `réserv…` **et** une URL (`http`/`www.`), **ET** le profil ne porte **aucun** indice de pré-ouverture (bio/légendes : `ouverture`, `opening soon`, `bientôt`… via `_has_opening_cue`) → établissement **déjà** en service. ⚠️ **villa.henriette_cabourg est justement une PRÉ-OUVERTURE** (bio « Ouverture 10 Juillet 2026 », « OPENING SOON » ; ouvre 2 jours après `TODAY`) qui teasait déjà sa résa — elle **n'est donc PAS captée** ici et retombe **au juge** (cf. Task 4, Step 5). Le veto pré-ouverture est **impératif** : sans lui ce helper tuerait un vrai `opening_soon` avant le juge (régression de rappel sur le signal privilégié). Le helper ne capte plus que de vraies résas d'établissements **ouverts** (couvert par un test synthétique + un test de non-capture d'une pré-ouverture).
- `_multi_city_in_bio(bio) -> bool` : ≥2 villes distinctes d'une liste connue sur une **même ligne** en liste (virgule/pipe/•) → marque multi-sites. Capte **cherescousinesbagels** (bio `Lyon 6, Paris 11`).
- `_RESA_HOSTS` += `"newtable"` : capte osabaita via `externalUrls` (`fr.newtable.com/...`) — 2e chemin, robustesse.
- `guard_verdict` ordre élargi : `_count_addresses_in_bio ≥ 2` **OU** `_multi_city_in_bio` → `chain_multisite` ; puis `postsCount > 150` / `_long_history` / `_has_hours_in_bio` / `_has_reservation_link` / `_has_reservation_in_bio` / `_has_reservation_in_posts` → `established` ; sinon `None`.
- **GARDE-FOU ABSOLU (non négociable)** : les 4 snapshots `opening` (`loumasrestaurant`, `chezgratien_hotelbistrospa`, `tregusto_sartrouville`, `brasseriedelafontainelourmarin`) doivent **toujours** renvoyer `guard_verdict is None` (test `test_opening_snapshots_pass_through_to_llm`, hérité). `_has_reservation_in_posts` porte déjà, par conception, un **veto pré-ouverture** (`_has_opening_cue` : `réserv` + URL **ET** aucun cue d'ouverture en bio/légendes) — c'est précisément ce qui empêche qu'une pré-ouverture teasant sa résa (les 4 openings, villa) soit captée. Si malgré tout l'ajout de `_has_reservation_in_posts` ou `_multi_city_in_bio` tranche un des 4 openings → **RED** : resserrer (élargir `_OPENING_CUES` ; multi-villes = ligne courte en liste) OU, en dernier recours, retirer la règle `_has_reservation_in_posts` (villa retombe alors, comme aujourd'hui, sur le juge). **Ne JAMAIS** relâcher pour « faire passer » un établi.
- **Chaînes sous 150 posts** : `cherescousinesbagels` (76 posts) est captée **au garde** par `_multi_city_in_bio`. `lemarcchiato` (139 posts) n'a **pas** de signal déterministe en bio (adresse mono-site `Vienne`, 2e lieu seulement en légendes) → **fallback JUGE** : la règle `chain_multisite` de `_DOSSIER_SYSTEM` est renforcée (« une 2e adresse / un 2e établissement de la MÊME enseigne mentionné dans les posts = chain_multisite »). Le juge voit les légendes (`au Marcchiato 2 rue boson`) dans le dossier. Non testable hors LLM → vérifié au gate live (T4).

- [ ] **Step 1: Write the failing tests**

Ajouter à `backend/tests/test_profile_guards.py` (imports des nouveaux helpers en tête) :

```python
from app.ingestion.profile_guards import (
    guard_verdict,
    _has_hours_in_bio,
    _has_reservation_link,
    _count_addresses_in_bio,
    _has_reservation_in_bio,
    _has_reservation_in_posts,
    _has_opening_cue,
    _multi_city_in_bio,
)
```

```python
def test_reservation_in_bio_helper():
    assert _has_reservation_in_bio("Réservation : 01 43 25 87 99")
    assert not _has_reservation_in_bio("Réservez votre table très bientôt")   # pas de tel
    assert not _has_reservation_in_bio("Café de spécialité, 5 rue du Marché")  # pas de résa


def test_multi_city_in_bio_helper():
    assert _multi_city_in_bio("Lyon 6, Paris 11")
    assert _multi_city_in_bio("Bordeaux | Toulouse")
    assert not _multi_city_in_bio("Bagels à Paris 11")                       # 1 ville
    assert not _multi_city_in_bio("Villeneuve d'Aveyron | Juillet 2026")     # 1 ville + date


def test_reservation_in_posts_helper():
    # En service : résa + URL et AUCUN indice d'ouverture -> True.
    assert _has_reservation_in_posts(
        {"latestPosts": [{"caption": "réservations sur notre site internet www.x.fr"}]})
    assert not _has_reservation_in_posts(
        {"latestPosts": [{"caption": "on ouvre bientôt, restez connectés !"}]})
    assert not _has_reservation_in_posts({"latestPosts": []})


def test_reservation_in_posts_ignores_preopening():
    # RÉGRESSION (garde-fou rappel opening) : une pré-ouverture qui tease DÉJÀ la
    # réservation en ligne ne doit JAMAIS être captée comme established -> None ->
    # elle reste au juge. Reproduit le vrai profil villa.henriette_cabourg.
    preopening = {
        "biography": "📅 Ouverture 10 Juillet 2026 ! #openingsoon",
        "latestPosts": [
            {"caption": "Rendez-vous pour les réservations sur www.villa-henriette.fr"},
            {"caption": "OPENING SOON !!! L'ouverture approche"},
        ],
    }
    assert _has_opening_cue(preopening)
    assert not _has_reservation_in_posts(preopening)


def test_osabaita_established_by_guard():
    snap = json.loads((SNAP / "osabaita.json").read_text(encoding="utf-8"))
    # Résa téléphone en bio + fr.newtable.com en externalUrls -> established, sans LLM.
    assert guard_verdict(snap, TODAY) == "established"


def test_villa_henriette_passes_to_judge():
    snap = json.loads((SNAP / "villa.henriette_cabourg.json").read_text(encoding="utf-8"))
    # villa.henriette est une PRÉ-OUVERTURE (bio « Ouverture 10 Juillet 2026 »,
    # « OPENING SOON », ouvre 2 jours après TODAY=2026-07-08) qui tease déjà la
    # résa en ligne. Le garde résa-posts est vetoé par `_has_opening_cue` -> None
    # -> villa retombe au juge (assertion de NON-RÉGRESSION : jamais captée au
    # garde, sinon perte d'un vrai opening_soon). Reste verte de bout en bout.
    assert guard_verdict(snap, TODAY) is None


def test_cherescousines_chain_by_guard():
    snap = json.loads((SNAP / "cherescousinesbagels.json").read_text(encoding="utf-8"))
    # Bio « Lyon 6, Paris 11 » = deux villes = marque multi-sites, sans LLM.
    assert guard_verdict(snap, TODAY) == "chain_multisite"
```

Ajouter à `backend/tests/test_judge_dossier.py` :

```python
def test_judge_prompt_mentions_second_address_chain():
    from app.ingestion.instagram import _DOSSIER_SYSTEM
    t = _DOSSIER_SYSTEM.lower()
    # La règle chaîne cible explicitement la 2e adresse d'une marque existante.
    assert "adresse" in t and ("2e adresse" in t or "nouvelle adresse" in t or "2e établissement" in t)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_profile_guards.py tests/test_judge_dossier.py -q`
Expected: FAIL — `ImportError` (`_has_reservation_in_bio`/`_has_reservation_in_posts`/`_has_opening_cue`/`_multi_city_in_bio` absents). Une fois les imports résolus : osabaita → `None`, cheres → `None` (pas encore captés) ; le prompt n'a pas encore la règle chaîne. **NB** : `test_villa_henriette_passes_to_judge` attend `None` et reste **vert de bout en bout** — c'est une assertion de **non-régression** (villa = pré-ouverture, jamais captée au garde), pas un test rouge.

- [ ] **Step 3: Write the implementation**

Dans `backend/app/ingestion/profile_guards.py` :

**a)** étendre `_RESA_HOSTS` et ajouter les constantes de détection (après les constantes existantes) :

```python
# Hébergeurs de réservation en ligne = établissement en exploitation.
_RESA_HOSTS = ("zenchef", "thefork", "lafourchette", "sevenrooms", "opentable",
               "resy", "newtable")

# Réservation : mot-clé (normalisé, sans accent) + téléphone FR / URL = en service.
_RESA_KW = "reserv"  # réservation / réserver / réservez
_PHONE_RE = re.compile(r"\b0\s?\d(?:[\s.\-]?\d\d){4}\b")   # 01 43 25 87 99, 0143258799…
_URL_RE = re.compile(r"https?://|www\.", re.IGNORECASE)

# Indices de PRÉ-OUVERTURE (normalisés, sans accent). Leur présence en bio OU
# dans une légende récente INTERDIT tout verdict « established » tiré d'une
# simple mention de réservation : une pré-ouverture ouvre souvent la résa en
# ligne AVANT d'ouvrir ses portes (ex. villa.henriette « Ouverture 10 Juillet »,
# « OPENING SOON »). Sans ce garde, un vrai lead `opening_soon` serait tué au
# garde avant d'atteindre le juge — régression de rappel sur le signal privilégié.
_OPENING_CUES = ("ouverture", "on ouvre", "ouvre bientot", "opening soon",
                 "openingsoon", "coming soon", "comingsoon", "bientot",
                 "prochainement")

# Villes connues (multi-sites en bio). Volontairement des grandes villes non
# ambiguës — évite de compter un simple gentilé comme une 2e adresse.
_CITY_TOKENS = ("paris", "lyon", "marseille", "bordeaux", "lille", "toulouse",
                "nantes", "nice", "strasbourg", "montpellier", "rennes", "cannes")
```

**b)** ajouter les trois helpers (après `_has_reservation_link`) :

```python
def _has_reservation_in_bio(bio: Optional[str]) -> bool:
    """Réservation active DANS LA BIO : mot-clé 'réserv…' + numéro de téléphone.
    Signal fort d'exploitation (une pré-ouverture n'affiche pas de ligne de résa).
    Cas ancré : osabaita ('Réservation : 01 43 25 87 99')."""
    if not bio:
        return False
    return _RESA_KW in _norm(bio) and bool(_PHONE_RE.search(bio))


def _has_opening_cue(profile: Dict[str, Any]) -> bool:
    """True si la bio OU une des ~12 dernières légendes annonce une (pré-)ouverture.
    Sert de veto au verdict `established` déterministe tiré d'une résa (une résa
    en ligne peut être teasée avant l'ouverture)."""
    texts = [profile.get("biography") or ""]
    texts += [(x.get("caption") or "") for x in (profile.get("latestPosts") or [])[:12]]
    joined = _norm(" \n ".join(texts))
    return any(cue in joined for cue in _OPENING_CUES)


def _has_reservation_in_posts(profile: Dict[str, Any]) -> bool:
    """Un post récent appelle à RÉSERVER via un site (réserv… + URL) ET le profil
    ne porte AUCUN indice de pré-ouverture (bio/légendes, cf. `_has_opening_cue`)
    = établissement DÉJÀ en service.

    Le veto pré-ouverture est IMPÉRATIF (ne PAS le retirer) : une pré-ouverture
    ouvre fréquemment la réservation en ligne avant d'ouvrir ses portes ; sans ce
    garde, ce helper capturerait un vrai `opening_soon` et le tuerait AVANT le
    juge — l'exact opposé du garde-fou absolu « recall opening ».

    NB : villa.henriette_cabourg — le cas qui avait motivé ce helper — est en
    réalité une PRÉ-OUVERTURE (bio « Ouverture 10 Juillet 2026 », « OPENING
    SOON », post « réservations sur notre site www.villa-henriette.fr »). Elle
    n'est donc **volontairement PAS** captée ici et retombe au juge. Ce helper ne
    capte plus que de vraies résas d'établissements déjà ouverts (test synthétique
    + régression de non-capture d'une pré-ouverture)."""
    if _has_opening_cue(profile):
        return False
    for x in (profile.get("latestPosts") or [])[:12]:
        cap = x.get("caption") or ""
        if _RESA_KW in _norm(cap) and _URL_RE.search(cap):
            return True
    return False


def _multi_city_in_bio(bio: Optional[str]) -> bool:
    """≥2 villes connues distinctes listées sur une MÊME ligne de bio (séparateur
    virgule / pipe / •) = marque multi-sites. Cas ancré : cherescousinesbagels
    ('Lyon 6, Paris 11'). Restreint aux lignes EN LISTE pour éviter les faux
    positifs (une phrase mentionnant deux villes n'est pas une liste d'adresses)."""
    if not bio:
        return False
    for line in bio.splitlines():
        if not any(sep in line for sep in (",", "|", "•")):
            continue
        t = _norm(line)
        cities = {c for c in _CITY_TOKENS if re.search(r"\b" + c + r"\b", t)}
        if len(cities) >= 2:
            return True
    return False
```

**c)** élargir `guard_verdict` :

```python
def guard_verdict(profile: Dict[str, Any], today: Optional[date] = None) -> Optional[str]:
    """Verdict déterministe du profil, ou None (à confier au juge LLM).
    Ordre : multi-adresses / multi-villes -> chain_multisite ; sinon volume /
    historique / horaires / résa (lien, bio, posts) -> established ; sinon None."""
    today = today or date.today()
    bio = profile.get("biography") or ""
    if _count_addresses_in_bio(bio) >= 2 or _multi_city_in_bio(bio):
        return "chain_multisite"
    posts_count = profile.get("postsCount")
    if isinstance(posts_count, int) and posts_count > POSTS_ESTABLISHED_HARD:
        return "established"
    if _long_history(profile, today):
        return "established"
    if _has_hours_in_bio(bio):
        return "established"
    if _has_reservation_link(profile):
        return "established"
    if _has_reservation_in_bio(bio):
        return "established"
    if _has_reservation_in_posts(profile):
        return "established"
    return None
```

Dans `backend/app/ingestion/instagram.py`, renforcer la règle `chain_multisite` de `_DOSSIER_SYSTEM`. Remplacer la ligne :

```python
    "- chain_multisite : marque à plusieurs adresses (décor répliqué, non "
    "prioritaire).\n"
```

par :

```python
    "- chain_multisite : marque à PLUSIEURS adresses OU en EXPANSION — plusieurs "
    "lieux listés, OU la bio/les posts annoncent une 2e adresse ou un 2e "
    "établissement de la MÊME enseigne (ex. '<enseigne> 2', 'nouvelle adresse', "
    "ouverture d'une succursale). Décor centralisé, non prioritaire (reste un "
    "lead 'en base').\n"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_profile_guards.py tests/test_judge_dossier.py -q` → PASS.
**Vérifier explicitement le garde-fou absolu** : `test_opening_snapshots_pass_through_to_llm` (les 4 `opening` → `None`) et `test_just_opened_monica_survives_guards` **restent verts**. S'ils tombent : un nouveau garde a capté une pré-ouverture — resserrer/retirer la règle fautive (cf. Interfaces), ne pas toucher au seuil.
Run: `python -m pytest tests/ -q` → tout vert.

- [ ] **Step 5: Commit**

```bash
git add backend/app/ingestion/profile_guards.py backend/app/ingestion/instagram.py backend/tests/test_profile_guards.py backend/tests/test_judge_dossier.py
git commit -m "feat(inventaire): precision gardes/juge (resa bio+posts, multi-villes, newtable, prompt chaine)"
```

---

### Task 4: Éval v2bis (buckets `en_base`, précision segment chaud ≥ 60 %) + docs + gates finaux

**Modèle d'exécution recommandé : opus**

**Files:**
- Modify: `backend/app/ingestion/eval/metrics.py` (fonction pure `hot_precision` + constantes segment chaud)
- Modify: `backend/app/ingestion/eval/run.py` (`TRUE_BUCKET` révisé, gate `hot_precision ≥ 0.60`, rapport avant/après)
- Modify: `backend/tests/test_eval.py` (test `hot_precision`)
- Modify: `backend/app/ingestion/eval/README.md` (buckets révisés + métrique honnête)
- Modify: `chr-signal-radar/docs/ARCHITECTURE.md` (§3 routage des labels ; §9 note 3bis)
- Modify: `chr-signal-radar/CLAUDE.md` (si besoin : inventaire complet + `lifecycle_label`)

**Interfaces:**
- `metrics.HOT_PRED = {"opening_soon", "just_opened"}` (prédictions du **segment chaud**) ; `metrics.hot_precision(label_pairs) -> Tuple[Optional[float], int, int]` : sur des paires `(vérité_mappée, label_prédit)`, `(vrais chauds / prédits chauds, tp, n)`. Un prédit chaud est **correct** si sa vérité ∈ `{opening_soon, just_opened}` (une prédiction `just_opened` sur un vrai `just_opened` **ne compte plus** comme faux positif — métrique honnête).
- `run.py` — `TRUE_BUCKET` **révisé** : `opening → a_contacter`, `just_opened → a_surveiller`, `established → en_base`, `chain_multisite → en_base`, `not_venue → ecarte`, `noise → ecarte`. `FRESH_LABELS` (projection binaire `a_contacter`, incl. `unknown`) **inchangée** → `recall_opening` **inchangé** (4/4 : l'espace de rappel reste `a_contacter`). Nouvelle métrique **honnête** = `hot_precision` (segment chaud strict, sans `unknown`).
- **GATES DURS** (dans `run_eval` + exit code de `main`) : `recall_opening == 1.0` (4/4, **inchangé, non négociable**) **ET** `hot_precision >= 0.60`. `precision_a_contacter` reste **calculée et publiée** (continuité vs briques 1-2) mais ne conditionne plus l'acceptation. Publier avant/après dans le commit / la PR.

- [ ] **Step 1: Write the failing tests**

Ajouter à `backend/tests/test_eval.py` :

```python
def test_hot_precision():
    from app.ingestion.eval.metrics import hot_precision
    pairs = [
        ("opening_soon", "opening_soon"),      # TP (vérité chaude, prédit chaud)
        ("just_opened", "just_opened"),        # TP (just_opened prédit sur vrai just_opened)
        ("established", "opening_soon"),       # FP (établi prédit chaud)
        ("not_venue", "noise"),                # hors segment chaud
        ("chain_multisite", "chain_multisite"),  # hors segment chaud
        ("opening_soon", "unknown"),           # hors segment chaud (unknown pas chaud)
    ]
    prec, tp, n = hot_precision(pairs)
    assert (tp, n) == (2, 3)
    assert abs(prec - 2 / 3) < 1e-9
    # Aucun prédit chaud -> None.
    assert hot_precision([("established", "established")]) == (None, 0, 0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_eval.py -q`
Expected: FAIL — `ImportError: cannot import name 'hot_precision'`.

- [ ] **Step 3: Write the implementation**

**a) `metrics.py`** — ajouter (après `label_confusion`) :

```python
# Segment « chaud » : prédictions à traiter en priorité (funnel v2bis).
HOT_PRED = {"opening_soon", "just_opened"}
# Vérités (déjà MAPPÉES : opening -> opening_soon) qui légitiment une prédiction
# chaude. unknown n'y est pas (une prédiction chaude sur un unknown vrai = FP).
HOT_TRUTH = {"opening_soon", "just_opened"}


def hot_precision(label_pairs: List[Pair]) -> Tuple[Optional[float], int, int]:
    """Précision du segment chaud = (vérité chaude parmi les prédits chauds) /
    prédits chauds. `label_pairs` = (vérité_mappée, label_prédit).
    -> (précision|None, vrais_positifs, total_prédits_chauds). None si aucun
    prédit chaud. Métrique HONNÊTE : un `just_opened` prédit sur un vrai
    `just_opened` compte comme vrai positif (plus un faux positif du recall opening)."""
    hot = [(truth, pred) for truth, pred in label_pairs if pred in HOT_PRED]
    if not hot:
        return None, 0, 0
    tp = sum(1 for truth, _ in hot if truth in HOT_TRUTH)
    return tp / len(hot), tp, len(hot)
```

**b) `run.py`** :

1. `TRUE_BUCKET` révisé + gate segment chaud (remplacer le dict `TRUE_BUCKET` et ajouter la constante après `GATE_MIN_PRECISION`) :

```python
# Plancher de précision a_contacter (métrique de continuité, publiée mais NON
# bloquante depuis la brique 3bis : le gate honnête est la précision du segment
# chaud, cf. GATE_HOT_PRECISION).
GATE_MIN_PRECISION = 0.33
# Gate honnête d'acceptation (brique 3bis) : précision du segment chaud
# (opening_soon/just_opened prédits) >= 60 %.
GATE_HOT_PRECISION = 0.60

# Bucket cible par label vérité (brique 3bis) : establi/chaîne = « en_base »
# (lead créé, segment froid) ; not_venue/noise = « ecarte » (pas de lead) ;
# opening = « a_contacter » ; just_opened = « a_surveiller ».
TRUE_BUCKET = {
    "opening": "a_contacter",
    "just_opened": "a_surveiller",
    "established": "en_base",
    "chain_multisite": "en_base",
    "not_venue": "ecarte",
    "noise": "ecarte",
}
```

2. `run_eval` — calculer `hot_precision` et le gate, l'ajouter au retour (remplacer la fin de `run_eval`, à partir du calcul de `labels_matrix`) :

```python
    labels_matrix = label_confusion(label_pairs)
    hot_prec, hot_tp, hot_n = hot_precision(label_pairs)

    gate_recall = report.recall_opening is not None and report.recall_opening >= GATE_RECALL_OPENING
    gate_precision = report.precision_a_contacter is not None and report.precision_a_contacter >= GATE_MIN_PRECISION
    gate_hot = hot_prec is not None and hot_prec >= GATE_HOT_PRECISION
    return {
        "report": report,
        "missing_snapshots": missing,
        "excluded_low_confidence": excluded_low,
        "predictions": buckets,
        "predicted_labels": predicted_labels,
        "label_by_handle": label_by_handle,
        "labels_matrix": labels_matrix,
        "hot_precision": hot_prec,
        "hot_tp": hot_tp,
        "hot_n": hot_n,
        "gate_recall_opening": gate_recall,
        "gate_precision": gate_precision,
        "gate_hot_precision": gate_hot,
        # ACCEPTATION brique 3bis : rappel opening 4/4 ET précision segment chaud >= 60 %.
        "gates_pass": gate_recall and gate_hot,
    }
```

Et l'import de `hot_precision` en tête de `run_eval` (avec `label_confusion`) :

```python
def run_eval(strict: bool = False) -> dict:
    from .metrics import label_confusion, hot_precision
```

3. `print_report` — remplacer la section finale (à partir de `ok = "OK" if ...`) par l'affichage segment chaud + gates :

```python
    print()
    hp = result.get("hot_precision")
    print(f"** PRÉCISION segment chaud : {_fmt_pct(hp)} **"
          f"   ({result.get('hot_tp', 0)} vrais / {result.get('hot_n', 0)} prédits opening_soon|just_opened)")
    ok = "OK" if result["gates_pass"] else "ÉCHEC"
    print(f"GATES : rappel opening>=100% = {result['gate_recall_opening']} | "
          f"précision chaud>=60% = {result['gate_hot_precision']}  -> {ok}")
    print(f"  (info) précision a_contacter>=33% = {result['gate_precision']}")
    print("=" * 60)
```

4. `main` — payload JSON complété (ajouter `hot_precision` au bloc `payload`) :

```python
        payload = {
            **result["report"].as_dict(),
            "labels_matrix": result["labels_matrix"],
            "hot_precision": result["hot_precision"],
            "gates_pass": result["gates_pass"],
            "missing_snapshots": result["missing_snapshots"],
            "excluded_low_confidence": result["excluded_low_confidence"],
        }
```

(`detailed_result` : `TRUE_BUCKET.get(label, "?")` renvoie désormais `en_base` pour établi/chaîne — aucun changement de code requis, la valeur suit le dict. Le champ `false_positive` binaire y reste pour l'inspection ; il n'est plus la métrique d'acceptation.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_eval.py -q` → PASS.
Run: `python -m pytest tests/ -q` → tout vert (aucun test unitaire n'appelle `classify()` en réseau).

- [ ] **Step 5: Gate d'acceptation live (LLM temp 0, `OPENAI_API_KEY` dans `backend/.env`)**

```
.venv\Scripts\python.exe -m app.ingestion.eval.run --json eval_v2bis_after.json
```

Attendu — `GATES : ... -> OK`, exit 0 :
- **Rappel opening = 100 % (4/4)** : `loumasrestaurant`/`chezgratien_hotelbistrospa`/`tregusto_sartrouville`/`brasseriedelafontainelourmarin` → `opening_soon` (tranchés par le juge : `guard_verdict is None` garanti par T3).
- **Précision segment chaud ≥ 60 %** : les seuls prédits `opening_soon`/`just_opened` doivent être de vrais `opening`/`just_opened`. Les anciens faux positifs `maisonaurea` (→ `not_venue`) et `chickntikka94` (→ `noise`) **sortent du segment chaud** (leurs `unknown` d'antan ne sont plus « chauds »).

Attendu qualitatif (matrice label×label ; noter la provenance — **garde** vs **juge** — car un compte tranché au garde ne voit jamais le juge) :
- **par garde-fou (T3)** : `cafe_mokaparis`/`cherescousinesbagels` → `chain_multisite` ; `osabaita`/`lemourerouge_cannes`/`lamerpaulettetrouville`/`lartemise_colmar` → `established` ; `maisonsaintaubain`/`un_lieu_une_ame_` → `established` (garde, PAS `not_venue` — le juge ne les voit pas ; restent `en_base`, sans impact gate) ; `imagine.trouville` → `established` (garde `_long_history`, perte de rappel `just_opened` **assumée**, cf. brique 3).
- **par juge** : `giorgina_restaurant` → `established` ; `maisonaurea` → `not_venue` ; `chickntikka94` → `noise` ; `lemarcchiato` → `chain_multisite` (règle « 2e adresse » du prompt, T3) ; `monica_stgermain` → `just_opened`/`unknown`.
- **par juge — cas à re-valider `villa.henriette_cabourg`** : n'est plus captée au garde (veto pré-ouverture, cf. T3) → tranchée par le juge. C'est une **pré-ouverture** (ouvre 2 jours après `TODAY`), donc le juge peut légitimement la classer **`opening_soon`**. ⚠️ **Tension à lever** : son label vérité était `established` — incohérent pour un compte qui ouvre dans 2 jours. Si le juge sort `opening_soon`, cela crée un **faux positif du segment chaud** vs un label vérité douteux : **corriger le label vérité de villa en `opening_soon`** (le comportement du juge est alors correct et compté comme vrai positif), ne PAS resserrer le garde pour re-capturer une pré-ouverture. Consigner la décision au gate.

Publier **avant/après** (baseline briques 1-2 : précision a_contacter ≈ 33 %, rappel 100 %). **Attendu live (non garanti — sortie de juge non déterministe, dénominateur ~4-5 prédits chauds)** : rappel 100 %, précision segment chaud **≈ 100 %** grâce à la sortie des 2 anciens faux positifs (`maisonaurea`, `chickntikka94`), **sous réserve** de la re-validation de villa ci-dessus. Le chiffre live est **indicatif** ; le gate déterministe reste le test unitaire `test_hot_precision`. Reporter systématiquement `hot_n` (dénominateur) à côté du ratio pour rendre visible un segment chaud fin ou vide. Pour le message de commit / la PR.
Si un `opening` sort `ecarte`/`en_base` : **STOP**, diagnostiquer (garde trop agressif — rejouer `test_opening_snapshots_pass_through_to_llm` — ou juge qui sur-étiquette). Ne relâcher aucun seuil.
Fail-soft : sans `OPENAI_API_KEY`, tout ce que les gardes ne tranchent pas devient `unknown` (`en_base`, hors segment chaud) → le segment chaud se vide, `hot_precision` = None → gate non mesurable ; la mesure se fait **avec** clé (comme les évals des briques 1-2). **Pas de run Apify live** (coût) : l'éval sur snapshots EST le gate.

- [ ] **Step 6: Documentation**

**`backend/app/ingestion/eval/README.md`** — mettre à jour la table des labels (colonne « bucket cible ») et la section métriques :
- `established`/`chain_multisite` → bucket cible **`en_base`** (lead créé, segment froid) — plus `ecarte`.
- `not_venue`/`noise` → **`ecarte`** (pas de lead).
- Ajouter la métrique honnête : « **précision du segment chaud** = vrais `opening`|`just_opened` parmi les prédits `opening_soon`|`just_opened` (un `just_opened` prédit sur un vrai `just_opened` n'est plus un faux positif) ; gate ≥ 60 %. Le rappel opening reste 4/4 sur la projection `a_contacter`. »

**`chr-signal-radar/docs/ARCHITECTURE.md`** :
- **§3** (« Source Instagram »), après l'étape 8 du funnel v2, ajouter le **routage brique 3bis** :

```
Routage label -> lead (brique 3bis) — inventaire complet : TOUT label devient un
lead SAUF not_venue/noise (verdict caché seul). opening_soon -> « ouverture
prochaine » (chaud) ; just_opened -> « création récente » (chaud) ; established
& chain_multisite & unknown -> signal NEUTRE « établissement en activité » (aucun
bonus de nature -> score bas, « en base »), + secondary « extension multi-sites »
pour les chaînes. Le label est PERSISTÉ (Opportunity.lifecycle_label, filtrable
via GET /api/opportunities?lifecycle_label=…). unknown n'est plus déguisé en
ouverture. Cache HandleVerdict et fenêtres INCHANGÉS.
```

- **§2** (Modèle de données), à la ligne `Opportunity`, mentionner `lifecycle_label` (label de cycle de vie persisté).
- **§9** (tableau des briques), ajouter la ligne :

```
| 3bis. Inventaire complet + précision | lifecycle_label persisté + filtre API ; routage de TOUS les labels en leads (établis/chaînes/unknown « en base », signal neutre) ; gardes/juge affinés (résa bio+posts, multi-villes, prompt chaîne) ; éval v2bis (buckets en_base, précision segment chaud >= 60 %) | **Fait** (2026-07-06) |
```

**`chr-signal-radar/CLAUDE.md`** (si présent et s'il décrit le funnel/les tables) : ajouter une phrase « brique 3bis : l'app est un inventaire complet — tout compte CHR devient un lead (sauf not_venue/noise), avec `lifecycle_label` persisté ; les établis/chaînes portent un signal neutre `établissement en activité` (score bas). » Aucune nouvelle variable d'env.

- [ ] **Step 7: Gates finaux (tout vert AVANT de committer)**

```
.venv\Scripts\python.exe -m pytest tests/ -q
.venv\Scripts\python.exe -m app.ingestion.eval.match_eval
.venv\Scripts\python.exe -m app.ingestion.eval.run --json eval_v2bis_after.json
```

Attendu : pytest **vert** ; match_eval **inchangée (8/9, 0 faux merge)** ; eval.run **`GATES : ... -> OK`** (rappel 100 %, précision segment chaud ≥ 60 %).

- [ ] **Step 8: Commit**

```bash
git add backend/app/ingestion/eval/metrics.py backend/app/ingestion/eval/run.py backend/tests/test_eval.py backend/app/ingestion/eval/README.md docs/ARCHITECTURE.md CLAUDE.md
git commit -m "feat(eval): eval v2bis (buckets en_base, precision segment chaud >=60%) + docs brique 3bis"
```

---

## Auto-relecture de cohérence inter-tâches

- **`lifecycle_label` de bout en bout** : `LeadCandidate` (T1, base.py) → `LABEL_ROUTING` remplit le champ (T2, run_instagram) → `_process_candidate` le persiste (T1) → `OpportunityList` l'expose + filtre API (T1). T2 dépend de T1 (le champ doit exister avant d'être routé). Ordre respecté.
- **Signal neutre** : `NEUTRAL_SIGNAL = "établissement en activité"` ajouté à `SIGNAL_TYPES` (T1) et consommé par `LABEL_ROUTING` (T2). Vérifié hors de toute famille de `services/scoring.py` (aucune modif de scoring) → score bas garanti ; test `test_established_becomes_low_score_lead` + `test_opening_outranks_established` (T2) le prouvent.
- **`extension multi-sites`** : chaîne exactement identique au libellé du delta-Sirene (`sirene_delta.py:89`) — harmonisation demandée. (La décision citait « extension multi-sites potentielle » ; on retient le libellé **existant** `extension multi-sites` pour ne pas fragmenter la famille de signaux — précision consignée.)
- **Cache inchangé** : aucune tâche ne touche `verdict_cache.py` ni les fenêtres `REVISIT_MONTHS`. Établis/chaînes deviennent des leads MAIS restent cachés 6 mois (leur verdict l'était déjà) ; `unknown`/`noise` 2 mois ; openings jamais mis en sommeil. `test_run_instagram_labels_leads_and_cache` (T2) et `test_run_instagram_skips_cached_handle` (hérité) verrouillent ce comportement.
- **Gardes → labels → leads → éval** : T3 fait sortir osabaita `established` et cheres `chain_multisite` **au garde** ; T2 les route en leads « en base » ; T4 les classe `en_base` (hors segment chaud) → ils n'entrent jamais dans `hot_precision`. Cohérent. `villa.henriette_cabourg` **ne sort PLUS au garde** (veto pré-ouverture, T3) : elle retombe au juge — c'est voulu (une pré-ouverture ne doit jamais être tranchée établie au garde), à re-valider au gate live (label vérité à corriger en `opening_soon`, cf. T4 Step 5). Le **garde-fou absolu** (4 openings → `None`) est un test hérité que T3 ne doit pas casser (Step 4 l'exige explicitement).
- **Rappel vs précision honnête** : `FRESH_LABELS` (incl. `unknown`) sert UNIQUEMENT à `recall_opening` (inchangé, 4/4) ; `HOT_PRED` (sans `unknown`) sert au **nouveau** gate. Les deux coexistent dans `run.py` sans se contredire — documenté dans le README (T4).
- **Signatures stables** : `guard_verdict`, `classify_profiles`, `run_instagram`, `judge_dossier`, `_process_candidate` gardent leur signature ; seuls s'ajoutent des helpers purs (T3) et des constantes de module (T2/T4). Aucun appelant existant cassé.
- **Tests hérités impactés** : uniquement `test_funnel_v2.py::test_run_instagram_labels_leads_and_cache` (MOKA devient un lead) — mis à jour en T2. `test_eval.py` gagne `test_hot_precision` (T4). Aucun autre test (bodacc/sirene/scoring/matcher) n'observe le routage Insta.

## Hors périmètre (briques/plans suivants)

- **Watchlist + réconciliation (brique 4)** : re-scrape des `opening_soon`/`just_opened`, re-matching périodique — le cache expose déjà `should_rejudge`, le scheduling reste hors périmètre.
- **UI** : affichage/tri par `lifecycle_label` et segment (chaud/en base) côté frontend — hors de ce plan (l'API le permet déjà via le filtre).
- **Agrandir la vérité terrain** (~100-150 comptes, tirage aléatoire de CHR établis) : nécessaire pour des seuils fiables (cf. caveats du README) — hors périmètre.
- **Détection déterministe de la 2e adresse `lemarcchiato`** (parsing des légendes) : repose sur le juge pour l'instant ; un garde dédié « 2e adresse en posts » viendra si le volume le justifie.

## Notes de revue (risques acceptés, hors correctifs)

Points relevés en relecture, **non bloquants** pour les gates (`recall_opening` 4/4 + `hot_precision ≥ 60 %`) et volontairement laissés en l'état — à traiter seulement si le libellé persisté doit gagner en précision ou si la vérité terrain s'agrandit.

- **`_multi_city_in_bio` — faux positifs de tagline (T3, mineur)** : la règle capte toute ligne de bio contenant un séparateur (`,` / `|` / `•`) et ≥2 tokens de `_CITY_TOKENS`. Une bio **mono-site** dont la tagline mentionne deux villes en prose (« esprit Marseille, à Paris ») ou une trajectoire (« Bordeaux | Paris ») serait donc étiquetée `chain_multisite`. **Sans impact gate/scoring** : `established` et `chain_multisite` routent tous deux vers `en_base` (même bucket, même score bas) — seule la précision du `lifecycle_label` persisté est concernée. Le jeu d'éval actuel ne déclenche pas ce cas. **Amélioration possible** (si le libellé doit être fiable) : n'accepter que des **segments courts en liste** (chaque segment séparé par le délimiteur < ~25 car., façon liste d'adresses) ou exiger la **co-occurrence d'un code postal / pin** ; puis ajouter un test négatif « bio mono-site citant deux villes en prose → `False` ». Non appliqué ici car cela change le design de la règle et le jeu d'éval ne l'exige pas.

- **`lifecycle_stage` dérivé vs `lifecycle_label` persisté (services/lifecycle.py, cosmétique)** : les leads Insta établis/chaîne/unknown portent `main_signal = "établissement en activité"`. `heat()` renvoie bien `froid` (correct, score/segment justes), mais `lifecycle_stage()` n'a pas de branche pour ce signal neutre et, faute de `review_count`/dates d'activité, retombe sur son défaut `ouvert récemment`. Le champ **dérivé** `lifecycle_stage` affichera donc `ouvert récemment` tandis que le champ **persisté** `lifecycle_label` dira `established`/`chain_multisite` — deux champs de cycle de vie **visiblement en désaccord** dans l'API/l'UI. **Purement cosmétique** : le label persisté est la source de vérité, le score et la chaleur sont corrects. **Correctif si la cohérence UI compte** : soit mapper le signal neutre vers `établi` dans `lifecycle_stage()`, soit faire préférer au frontend `lifecycle_label` au `lifecycle_stage` dérivé pour les leads d'origine Instagram. Non appliqué ici (aucun effet sur les gates).
