# Population « architectes d'intérieur » (A1) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL — utiliser `superpowers:subagent-driven-development` (recommandé) ou `superpowers:executing-plans` pour exécuter ce plan tâche-par-tâche. Étapes en `- [ ]` pour le suivi. Chaque tâche porte un **Modèle d'exécution recommandé** pour l'orchestrateur.

**Goal:** Ajouter une **seconde population** de leads — les **architectes d'intérieur** (prescripteurs de luminaires/mobilier) — À CÔTÉ du CHR, sans jamais toucher au funnel CHR ni à ses évals. Découverte Instagram-first (hashtags dédiés) → garde-fous déterministes (hors-cible sûr) → **juge prescripteur** LLM unitaire (labels `studio_actif | studio_dormant | compte_perso | hors_cible`) → **tiering de priorité** (T1 studio tagué sur un chantier CHR détecté ; T2 studio_actif avec preuve hospitality/CHR ; T3 studio_actif générique). Les leads architectes portent une colonne `population='architecte'`, un `main_signal` NEUTRE `prescripteur actif` (hors familles de scoring CHR), et NE passent PAS par le classifieur CHR ni le juge CHR.

**Hypothèse produit validée (Alexis, propriétaire d'Ambient Home) :** un studio d'archi d'intérieur est en permanence en projet → toujours partiellement *in-market*. Le « moment » (studio tagué sur un chantier CHR détecté, post « nouveau projet ») n'est PAS un filtre mais un **BOOSTER de priorité** (tier). Cible : **VOLUME MAX** national (PAS de filtre Île-de-France), studios/indépendants > gros cabinets à appels d'offres.

**Fondé sur la sonde** (`.superpowers/sdd/sonde-architectes.json` + 15 profils bruts) : 3 hashtags mesurés (`architectedinterieur` 70 %, `architecturedinterieure` 70 %, `agencement` 80 % de comptes distincts ; recouvrement inter-tags quasi nul → les combiner multiplie la couverture). Motifs discriminants et pièges de la sonde encodés dans les gardes/juge (cf. « Décisions tranchées par la sonde » ci-dessous).

**Architecture (delta vs CHR, tout ADDITIF) :** un flux parallèle `run_prescripteurs` (miroir de `run_instagram`) : `discover_prescripteurs` (heuristique bio/nom archi, national, PAS de filtre CHR/IdF) → `verdict_cache.should_rejudge` (table `HandleVerdict` RÉUTILISÉE) → `scrape_profiles` (infra Apify RÉUTILISÉE) → `classify_prescripteurs` (gardes `prescriber_guards` → matcher SIRET RÉUTILISÉ, CHR-gated donc no-op propre pour les archis → `judge_prescripteur` → tiering) → `verdict_cache.upsert` → `LeadCandidate(population='architecte')` + `_process_candidate` (branche population-aware qui CONTOURNE le classifieur CHR). Enrichissement contact (`run_contact_enrich`) et matcher restent population-agnostiques.

**Tech Stack:** Python 3.9 (`Optional[X]`/`Dict`/`List`/`Set` de `typing`, **jamais** `X | None`), SQLModel/SQLite (migration légère `ALTER TABLE ADD COLUMN` dans `database.py`), OpenAI (optionnel, fail-soft, `OPENAI_JUDGE_MODEL` — MÊME modèle que le juge CHR), pytest. Docstrings/commentaires/prompts **en français**. Réutilise `_age_label` (`siret_matcher`), le pattern `_USE_ENV`/client injectable, `HandleVerdict`/`verdict_cache`.

## Global Constraints

- **Python 3.9** ; **fail-soft partout** (pas de clé/erreur LLM → label dégradé `studio_actif`/confiance `basse`, gardé et NON caché → protège le VOLUME ; pas de token Apify → scrape `{}`). **Aucun appel réseau/LLM réel dans les tests unitaires** : clients/`match_fn`/`scrape_profiles`/`tagged_studios` injectés (pattern `_FakeClient` de `tests/test_judge_dossier.py`). Le seul LLM live autorisé est l'éval prescripteurs (gate d'acceptation, T6).
- **Répertoires** : `python`/`pytest` depuis `chr-signal-radar/backend` avec `.venv\Scripts\python.exe` ; `git` depuis la racine `chr-signal-radar/` (les chemins `backend/...` des commits sont relatifs à cette racine). Branche **`feature/population-architectes`**. **Pas de push, pas de `--no-verify`.**
- `python -m pytest tests/ -q` **vert à la fin de CHAQUE tâche**.
- **ÉVALS CHR INTACTES (non négociable)** : `app.ingestion.eval.run` (gates `recall_opening == 1.0`, `hot_precision >= 0.60` inchangés) et `app.ingestion.eval.match_eval` (**8/9, 0 faux merge**) NE DOIVENT PAS BOUGER. Toutes les modifications de `scoring.py`, `verdict_cache.py`, `instagram.py`, `pipeline.py`, `models.py` sont **STRICTEMENT ADDITIVES** : les leads/labels CHR n'émettent jamais les nouveaux libellés/labels, donc leurs scores/verdicts sont bit-à-bit identiques. Vérifié à chaque tâche par le pytest complet, et au gate final par le CLI d'éval CHR.
- **TDD strict** : tests d'abord (RED), puis implémentation (GREEN), puis commit avec le message exact fourni.
- **Coût Apify borné** : les scrapes de sonde/run sont assumés mais bornés (`limit` modestes, cache `HandleVerdict` amortit les runs répétés).
- **Créer la branche avant la Task 1** (depuis la racine) :

```bash
git checkout -b feature/population-architectes
```

**Espace de labels prescripteurs (fixé) :** `studio_actif | studio_dormant | compte_perso | hors_cible`.
Mapping label → lead : `studio_actif`/`studio_dormant` → **lead** ; `compte_perso`/`hors_cible` → **verdict caché, PAS de lead**.
**Tiers de priorité** (studio_actif uniquement) : `T1` = studio tagué sur un chantier CHR détecté par la machine (accroche « j'ai vu votre projet X ») ; `T2` = studio_actif avec preuve hospitality/CHR dans son portfolio ; `T3` = studio_actif générique.

## Décisions tranchées par la sonde (à lire avant de coder)

1. **PAS de `studio_actif` déterministe.** Le titre exact « architecte d'intérieur » en bio est le signal le plus fort MAIS **insuffisant seul** : `divnaanni` le porte mais est `compte_perso` (email @gmail, cadence irrégulière, ton « mon univers ») ; `habiteretgrandir` le porte mais est `hors_cible` (coach HOMER®). → Les gardes ne font QUE du `hors_cible`/`noise` déterministe ; `studio_actif`/`studio_dormant`/`compte_perso` sont laissés au juge (avec âges/cadence PRÉCALCULÉS).
2. **`hors_cible` déterministe SÛR** (gardes) : (a) **formation/coaching** — `coach`, `cours privé(s)`, `formation`, `masterclass`, `mentorat` (grounded `endora.studio3d` « Cours privés SketchUp… pour les architectes d'intérieur » = B2B2B ; `habiteretgrandir` « coach HOMER® ») ; (b) **artisan/fabricant voisin** — `menuiserie`/`ébéniste`/`tapissier`/`serrurier`/`marbrier` en bio SANS titre archi (grounded `atelierlesimple`, `cotefauteuils`) ; (c) **non-prescripteur** — `graphiste`/`webdesign`/`community manager`/`photographe`/`webmagazine`/`UX/UI` ; (d) **étranger** — domaine `.be/.ch/.ca` (piège CHR connu, absent de l'échantillon archi mais garde léger conservé).
3. **RÉCENCE précalculée, pas déterministe.** `helene.gombert` (447 posts, 17 k abonnés, email pro) a l'apparence d'un studio de premier plan mais son feed est incohérent (posts 2024/2025 mêlés à quelques posts 2026) → `studio_dormant` selon la sonde. La récence est **ambiguë** → on la PRÉCALCULE en code (âge du post le plus récent + cadence 90 j) et on la donne au juge, qui tranche `studio_actif` vs `studio_dormant` (jamais un garde dur).
4. **National, PAS d'IdF.** Les hashtags archi sont nationaux (Sables-d'Olonne, Bordeaux, Compiègne, Pays Basque…), pas parisiens. Conforme à la décision « VOLUME MAX » : `discover_prescripteurs` n'applique **aucun** filtre géo ni CHR.
5. **Email TOUJOURS en texte libre.** `businessEmail`/`public_email` sont systématiquement `None` (15/15) → on parse l'email depuis la bio/les posts (juge). Domaine propre (`contact@studio.com`) = signal pro fort ; `@gmail.com` penche `compte_perso`.
6. **Preuve hospitality (T2) réelle.** `atelierdularge` collabore avec `@hotel_restaurant_locean` (CHR) ; `bifur.architecture` a un pôle « BIFUR COMMERCE » (retail) → certains archis prescrivent DÉJÀ pour du CHR/retail. Le juge extrait un booléen `hospitality_proof`.
7. **Matcher SIRET CHR-gated → no-op propre pour les archis.** `pick_by_name`/le pool d'arbitre exigent `classify_naf(naf)` CHR ; un NAF archi (71.11Z/74.10Z) n'est jamais accepté → `match()` renvoie `None` **proprement** (pas de crash). En A1 le juge prescripteur travaille sur le profil seul (registre `None`). Élargir le NAF-gate du matcher pour enrichir les archis en SIREN = **hors périmètre A1** (brique A2).
8. **CSV d'éval SÉPARÉ.** `architectes_groundtruth.csv` + `snapshots_architectes/` + module d'éval dédié → l'éval CHR (`instagram_groundtruth.csv`, `snapshots/`, `run.py`) reste **bit-à-bit intacte**. Seed du CSV = les 15 comptes classés par la sonde.

**Routage label → lead (encodé en `PRESCRIBER_ROUTING`) :**

| label | tier | lead ? | `main_signal` | `secondary_signals` | `lifecycle_label` |
|---|---|---|---|---|---|
| `studio_actif` | T1 | oui (prioritaire) | `prescripteur actif` *(neutre)* | `projet CHR détecté` | `studio_actif` |
| `studio_actif` | T2 | oui | `prescripteur actif` *(neutre)* | `portfolio hospitality/CHR` | `studio_actif` |
| `studio_actif` | T3 | oui (en base) | `prescripteur actif` *(neutre)* | — | `studio_actif` |
| `studio_dormant` | — | oui (en base, bas) | `prescripteur actif` *(neutre)* | `studio en sommeil` | `studio_dormant` |
| `compte_perso` | — | **non** (cache seul) | — | — | — |
| `hors_cible` | — | **non** (cache seul) | — | — | — |
| `noise` | — | **non** (cache seul) | — | — | — |

Le `main_signal` neutre `prescripteur actif` est membre d'**aucune** famille de scoring de nature → aucun bonus d'ouverture/reprise. Les libellés de tier `projet CHR détecté` (+3) et `portfolio hospitality/CHR` (+2) sont des familles **NOUVELLES** que les leads CHR n'émettent JAMAIS → scores CHR inchangés (cf. Global Constraints). Ordre de tri obtenu : T1 > T2 > T3 > dormant.

---

### Task 1: Colonne `population` — persistance + contournement propre du classifieur CHR + API/UI

**Modèle d'exécution recommandé : sonnet**

**Files:**
- Modify: `backend/app/models.py` (champ `Opportunity.population` + entrées `SIGNAL_TYPES`)
- Modify: `backend/app/database.py` (migration `ADD COLUMN population`)
- Modify: `backend/app/ingestion/base.py` (champ `LeadCandidate.population`)
- Modify: `backend/app/ingestion/pipeline.py` (`_process_candidate` : branche population-aware + persistance ; `_merge_corroboration` ; `NEEDS_BY_TYPE`)
- Modify: `backend/app/schemas.py` (`OpportunityList.population`)
- Modify: `backend/app/routes/opportunities.py` (paramètre + filtre `population`)
- Modify: `backend/app/routes/dashboard.py` (`get_stats` : filtre `population`, défaut `'chr'`)
- Modify: `backend/app/main.py` (meta `populations`)
- Modify: `backend/../frontend/lib/types.ts`, `frontend/lib/api.ts`, `frontend/lib/labels.ts`, `frontend/components/Badges.tsx`, `frontend/app/opportunities/page.tsx` (badge + filtre population ; labels prescripteurs)
- Create: `backend/tests/test_population.py`

**Interfaces:**
- `Opportunity.population: str = Field(default="chr", index=True)` — `'chr'` (défaut, toutes les fiches existantes/BODACC/Sirene) | `'architecte'`.
- `LeadCandidate.population: str = "chr"` — porté de bout en bout.
- `SIGNAL_TYPES` += `"prescripteur actif"`, `"projet CHR détecté"`, `"portfolio hospitality/CHR"`, `"studio en sommeil"` (pour l'exposition meta ; les familles de scoring arrivent en T3).
- `_process_candidate` : **branche population-aware** — si `cand.population == "architecte"`, l'`establishment_type` est pris tel quel (`cand.establishment_type or "architecte d'intérieur"`) et le classifieur CHR (`classify_naf`/`classify`) est **contourné** (un NAF archi 71.11Z renverrait `None` et DROPPERAIT le lead à tort). L'enricher Sirene est **sauté** pour les archis (comme pour `source == "sirene"`) : en A1 ils n'ont pas de SIREN (matcher CHR-gated) et l'enrichissement CHR ne s'applique pas. `population` persisté à la création, à l'upsert même-source, et via `_merge_corroboration` (ne remplit que le trou, jamais n'écrase par une valeur différente).
- `GET /api/opportunities?population=architecte` → filtre `Opportunity.population == …`.
- `GET /api/dashboard/stats` : **filtre `population`, défaut `'chr'`** — le dashboard ne compte QUE le CHR par défaut (les leads architectes de T4/T6 ne polluent NI `total_opportunities`, NI `by_signal`, NI le top 5 `hottest`). `?population=architecte` cible les archis, `?population=` (vide) = toutes populations. Sans ce filtre, un lead archi T1/T2 (score élevé) pourrait déloger un lead CHR chaud du top 5.
- Meta : `"populations": ["chr", "architecte"]`.
- **Cette tâche n'AJOUTE aucun lead architecte** (pas de découverte/run) : la colonne existe, `'chr'` partout, filtrable. Migration idempotente (même convention que `lifecycle_label`).

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_population.py
"""Tests de la colonne population (A1, T1) : migration, persistance, contournement
du classifieur CHR pour les architectes, exposition API + filtre. Aucun réseau."""
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


def test_signal_types_contains_prescriber_labels():
    from app.models import SIGNAL_TYPES
    for s in ("prescripteur actif", "projet CHR détecté",
              "portfolio hospitality/CHR", "studio en sommeil"):
        assert s in SIGNAL_TYPES


def test_leadcandidate_defaults_to_chr():
    c = LeadCandidate(source="bodacc", source_ref="x", establishment_name="X",
                      city="Paris", main_signal="ouverture prochaine",
                      detection_date=date(2026, 7, 10))
    assert c.population == "chr"


def test_process_candidate_persists_population_architecte():
    with Session(_engine()) as s:
        cand = LeadCandidate(
            source="instagram", source_ref="studio1", establishment_name="Studio X",
            city="Bordeaux", address="", main_signal="prescripteur actif",
            detection_date=date(2026, 7, 10), establishment_type="architecte d'intérieur",
            lifecycle_label="studio_actif", population="architecte",
        )
        _process_candidate(s, cand, IngestStats(source="instagram"), set(), enricher=None)
        s.commit()
        opp = s.exec(select(Opportunity).where(Opportunity.source_ref == "studio1")).first()
        assert opp is not None
        assert opp.population == "architecte"
        assert opp.establishment_type == "architecte d'intérieur"


def test_architecte_bypasses_chr_classifier_even_with_non_chr_naf():
    # NAF archi (71.11Z) : le classifieur CHR renverrait None et dropperait le lead.
    # La branche population-aware doit le GARDER (type pris tel quel).
    with Session(_engine()) as s:
        cand = LeadCandidate(
            source="instagram", source_ref="studio2", establishment_name="Atelier Y",
            city="Lyon", address="", main_signal="prescripteur actif",
            detection_date=date(2026, 7, 10), establishment_type="architecte d'intérieur",
            naf="71.11Z", population="architecte",
        )
        _process_candidate(s, cand, IngestStats(source="instagram"), set(), enricher=None)
        s.commit()
        opp = s.exec(select(Opportunity).where(Opportunity.source_ref == "studio2")).first()
        assert opp is not None and opp.population == "architecte"


def test_chr_lead_still_dropped_by_non_chr_naf():
    # Non-régression : un lead CHR (population par défaut) avec un NAF non-CHR
    # reste DROPPÉ (le contournement ne s'applique QU'aux architectes).
    with Session(_engine()) as s:
        cand = LeadCandidate(
            source="bodacc", source_ref="holding1", establishment_name="Holding Immo",
            city="Paris", address="", main_signal="ouverture prochaine",
            detection_date=date(2026, 7, 10), naf="68.20A",  # immobilier, non-CHR
            classification_text="hôtel restaurant SCI",
        )
        _process_candidate(s, cand, IngestStats(source="bodacc"), set(), enricher=None)
        s.commit()
        assert s.exec(select(Opportunity).where(Opportunity.source_ref == "holding1")).first() is None


def test_api_filters_by_population():
    with Session(_engine()) as s:
        for ref, pop, sig, etype in [
            ("chr1", "chr", "ouverture prochaine", "restaurant"),
            ("arc1", "architecte", "prescripteur actif", "architecte d'intérieur"),
        ]:
            _process_candidate(
                s, LeadCandidate(source="instagram", source_ref=ref, establishment_name=ref,
                                 city="Paris", address="", main_signal=sig,
                                 detection_date=date(2026, 7, 10), establishment_type=etype,
                                 population=pop),
                IngestStats(source="instagram"), set(), enricher=None)
        s.commit()
        got = list_opportunities(session=s, population="architecte")
        assert [o.source_ref for o in got] == ["arc1"]


def test_dashboard_stats_default_excludes_architectes():
    # Le dashboard CHR ne doit PAS être pollué par les leads architectes : par
    # défaut get_stats filtre population=='chr' (compteurs, by_signal, hottest).
    from app.routes.dashboard import get_stats
    with Session(_engine()) as s:
        for ref, pop, sig, etype in [
            ("chrA", "chr", "ouverture prochaine", "restaurant"),
            ("chrB", "chr", "ouverture prochaine", "bar"),
            ("arcA", "architecte", "prescripteur actif", "architecte d'intérieur"),
        ]:
            _process_candidate(
                s, LeadCandidate(source="instagram", source_ref=ref, establishment_name=ref,
                                 city="Paris", address="", main_signal=sig,
                                 detection_date=date(2026, 7, 10), establishment_type=etype,
                                 population=pop),
                IngestStats(source="instagram"), set(), enricher=None)
        s.commit()
        default = get_stats(session=s)  # défaut 'chr'
        assert default.total_opportunities == 2
        assert all(b.label != "prescripteur actif" for b in default.by_signal)
        assert all(o.population == "chr" for o in default.hottest)
        assert get_stats(session=s, population="architecte").total_opportunities == 1
        assert get_stats(session=s, population="").total_opportunities == 3  # toutes


def test_migration_adds_population_column(tmp_path):
    from sqlalchemy import create_engine as ce, inspect, text
    import app.database as db
    url = f"sqlite:///{tmp_path/'legacy.db'}"
    old = ce(url)
    with old.begin() as conn:
        conn.execute(text("CREATE TABLE opportunities (id INTEGER PRIMARY KEY, "
                          "establishment_name VARCHAR, establishment_type VARCHAR, "
                          "city VARCHAR, address VARCHAR, main_signal VARCHAR, "
                          "detection_date DATE, estimated_timing VARCHAR)"))
    old.dispose()
    orig_engine, orig_url = db.engine, db.DATABASE_URL
    db.engine, db.DATABASE_URL = ce(url), url
    try:
        db._run_lightweight_migrations()
        cols = {c["name"] for c in inspect(db.engine).get_columns("opportunities")}
        assert "population" in cols
    finally:
        db.engine.dispose()
        db.engine, db.DATABASE_URL = orig_engine, orig_url
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_population.py -q`
Expected: FAIL — `TypeError` (`LeadCandidate` sans `population`), `AttributeError`/`AssertionError` (colonne/param/meta absents), `test_architecte_bypasses...` échoue (lead droppé par le classifieur CHR), et `test_dashboard_stats_default_excludes_architectes` échoue (`get_stats` sans paramètre `population`, ou lead archi compté).

- [ ] **Step 3: Write the implementation**

**a) `models.py`** — dans `SIGNAL_TYPES`, ajouter à la fin (après `"établissement en activité"`) :

```python
    "établissement en activité",
    # Population ARCHITECTES (A1) : signal NEUTRE des prescripteurs (hors familles
    # de scoring CHR -> aucun bonus de nature) + libellés de tier (bonus ajoutés
    # en T3, jamais émis par les leads CHR -> scores CHR inchangés).
    "prescripteur actif",
    "projet CHR détecté",
    "portfolio hospitality/CHR",
    "studio en sommeil",
]
```

Dans la classe `Opportunity`, juste après le champ `lifecycle_label` :

```python
    lifecycle_label: Optional[str] = Field(default=None, index=True)

    # Population du lead : 'chr' (défaut, toutes les sources registre + funnel CHR)
    # ou 'architecte' (prescripteurs d'archi d'intérieur, A1). Les architectes NE
    # passent PAS par le classifieur CHR ni le juge CHR ; ils ont leur propre
    # découverte/juge/tiering et un main_signal neutre 'prescripteur actif'.
    population: str = Field(default="chr", index=True)
```

**b) `database.py`** — ajouter au dict `additions` (après `lifecycle_label`) :

```python
        "lifecycle_label": "ALTER TABLE opportunities ADD COLUMN lifecycle_label VARCHAR",
        "population": "ALTER TABLE opportunities ADD COLUMN population VARCHAR DEFAULT 'chr'",
    }
```

**c) `base.py`** — dans `LeadCandidate`, après `lifecycle_label` :

```python
    lifecycle_label: Optional[str] = None
    # Population du lead : 'chr' (défaut) ou 'architecte' (prescripteur, A1).
    population: str = "chr"
    decision_maker: Optional[str] = None
```

**d) `pipeline.py`** :

Dans `NEEDS_BY_TYPE`, ajouter une entrée prescripteur (après `"traiteur"`) :

```python
    "traiteur": ["éclairage de boutique", "mobilier de présentation"],
    # Prescripteur (A1) : besoins orientés prescription/sourcing, pas aménagement
    # d'une salle en propre.
    "architecte d'intérieur": ["prescription luminaires", "mobilier sur-mesure", "sourcing produits"],
}
```

Dans `_process_candidate`, remplacer le bloc enricher + classification CHR. Aujourd'hui :

```python
    if enricher is not None and cand.source != "sirene":
        enricher.enrich(cand)
        ...
    # 2. Classification CHR.
    text = " ".join(filter(None, [cand.classification_text, cand.establishment_name]))
    if cand.naf:
        etype = classify_naf(cand.naf, text)  # NAF fait autorité
    elif cand.establishment_type:
        etype = cand.establishment_type
    else:
        etype = classify(text)
    if not etype:
        return  # pas du CHR pertinent
    stats.chr_matched += 1
```

Remplacer par (l'enricher saute AUSSI les architectes ; la classification les CONTOURNE) :

```python
    # ARCHITECTES (A1) : population dédiée. Ils NE passent NI par l'enricher Sirene
    # (données/NAF CHR non pertinents ; pas de SIREN en A1, matcher CHR-gated) NI
    # par le classifieur CHR (un NAF archi 71.11Z renverrait None -> lead droppé à
    # tort). Le type est pris tel quel (déjà validé « archi » à la découverte).
    is_architecte = cand.population == "architecte"
    if enricher is not None and cand.source != "sirene" and not is_architecte:
        enricher.enrich(cand)
        # Reprise : dater l'origine réelle du local via le précédent exploitant.
        if cand.previous_siren:
            prev = enricher.lookup(cand.previous_siren)
            cand.venue_origin_date = _ymd((prev or {}).get("date_creation"))
        if cand.enriched:
            stats.enriched += 1
        if cand.closed:
            stats.skipped_closed += 1
            return  # établissement fermé : on n'en fait pas un lead

    # 2. Classification.
    if is_architecte:
        etype = cand.establishment_type or "architecte d'intérieur"
    else:
        # Classification CHR : si on a un NAF (enrichi), il fait AUTORITÉ.
        text = " ".join(filter(None, [cand.classification_text, cand.establishment_name]))
        if cand.naf:
            etype = classify_naf(cand.naf, text)  # NAF fait autorité
        elif cand.establishment_type:
            etype = cand.establishment_type  # déjà validé CHR (ex. découverte Instagram)
        else:
            etype = classify(text)
    if not etype:
        return  # pas du CHR pertinent
    stats.chr_matched += 1
```

> **Note de cohérence** : le bloc `enricher.enrich(...)` original (avec `previous_siren`/`venue_origin_date`/`stats.enriched`/`stats.skipped_closed`) est déplacé tel quel sous la garde `and not is_architecte`. Ne rien perdre de sa logique.

Dans la branche **création** (`opp = Opportunity(...)`), ajouter après `lifecycle_label=cand.lifecycle_label,` :

```python
        lifecycle_label=cand.lifecycle_label,
        population=cand.population,
```

Dans la branche **upsert même-source** (`if existing:`), ajouter après la ligne `existing.lifecycle_label = cand.lifecycle_label or existing.lifecycle_label` :

```python
        existing.lifecycle_label = cand.lifecycle_label or existing.lifecycle_label
        # La population ne change pas d'un run à l'autre pour un même handle ;
        # on la (re)pose défensivement (une ancienne fiche pré-A1 est 'chr').
        existing.population = cand.population or existing.population
```

Dans `_merge_corroboration`, après `opp.lifecycle_label = opp.lifecycle_label or cand.lifecycle_label` :

```python
        opp.lifecycle_label = opp.lifecycle_label or cand.lifecycle_label
        # Fusion cross-source : ne jamais reclasser la population par une valeur
        # différente (un lead 'chr' corroboré ne devient pas 'architecte').
        opp.population = opp.population or cand.population
```

**e) `schemas.py`** — dans `OpportunityList`, après `lifecycle_label` :

```python
    lifecycle_label: Optional[str] = None
    population: str = "chr"
```

**f) `routes/opportunities.py`** — ajouter le paramètre (après `lifecycle_label`) et le filtre :

```python
    lifecycle_label: Optional[str] = None,
    population: Optional[str] = None,
    sort_by: str = "score",
```

et, dans le corps (après le bloc `if lifecycle_label:`) :

```python
    if lifecycle_label:
        query = query.where(Opportunity.lifecycle_label == lifecycle_label)
    if population:
        query = query.where(Opportunity.population == population)
```

**f-bis) `routes/dashboard.py`** — filtrer `get_stats` par population (défaut `'chr'`) pour que les leads architectes ne polluent pas le dashboard CHR. Ajouter `from typing import Optional` en tête, puis remplacer la signature et la première ligne de `get_stats` :

```python
@router.get("/stats", response_model=DashboardStats)
def get_stats(session: Session = Depends(get_session),
              population: Optional[str] = "chr"):
    # Par défaut, le dashboard ne compte QUE le CHR : les leads architectes (A1)
    # ne polluent NI les compteurs, NI by_signal, NI le top 5 « hottest ».
    # ?population=architecte cible les archis ; ?population= (vide) = toutes.
    query = select(Opportunity)
    if population:
        query = query.where(Opportunity.population == population)
    opportunities = session.exec(query).all()
```

(Le reste du corps de `get_stats` est inchangé — il opère sur `opportunities`.)

**g) `main.py`** — dans `get_meta`, ajouter la clé :

```python
        "statuses": STATUSES,
        "populations": ["chr", "architecte"],
        "cities": cities,
```

**h) Frontend** :

`frontend/lib/types.ts` — dans `OpportunityList`, après `lifecycle_label` :

```typescript
  lifecycle_label: string | null;
  population: string;
```

`frontend/lib/api.ts` — dans `OpportunityFilters`, après `lifecycle_label` :

```typescript
  lifecycle_label?: string;
  population?: string;
```

`frontend/lib/labels.ts` — ajouter les libellés population ET compléter les labels de cycle de vie prescripteurs (badge). Après `LIFECYCLE_LABEL_STYLES` :

```typescript
// Population du lead (A1) : CHR (défaut) ou architectes d'intérieur (prescripteurs).
export const POPULATION_LABELS: Record<string, string> = {
  chr: "CHR",
  architecte: "Architecte",
};

export const POPULATION_STYLES: Record<string, string> = {
  chr: "bg-slate-100 text-slate-500 ring-slate-200",
  architecte: "bg-indigo-50 text-indigo-700 ring-indigo-200",
};
```

et étendre `LIFECYCLE_LABEL_LABELS` / `LIFECYCLE_LABEL_STYLES` (les leads architectes réutilisent la colonne `lifecycle_label`) :

```typescript
export const LIFECYCLE_LABEL_LABELS: Record<string, string> = {
  opening_soon: "Ouverture prochaine",
  just_opened: "Vient d'ouvrir",
  renovation: "Rénovation en cours",
  established: "Établi",
  chain_multisite: "Chaîne / multi-sites",
  unknown: "À qualifier",
  // Population architectes (A1).
  studio_actif: "Studio actif",
  studio_dormant: "Studio en sommeil",
};
```

```typescript
export const LIFECYCLE_LABEL_STYLES: Record<string, string> = {
  opening_soon: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  just_opened: "bg-cyan-50 text-cyan-700 ring-cyan-200",
  renovation: "bg-orange-50 text-orange-700 ring-orange-200",
  established: "bg-slate-100 text-slate-600 ring-slate-200",
  chain_multisite: "bg-violet-50 text-violet-700 ring-violet-200",
  unknown: "bg-slate-100 text-slate-500 ring-slate-200",
  studio_actif: "bg-indigo-50 text-indigo-700 ring-indigo-200",
  studio_dormant: "bg-slate-100 text-slate-500 ring-slate-200",
};
```

`frontend/components/Badges.tsx` — importer et ajouter le badge population. Dans l'import depuis `@/lib/labels`, ajouter `POPULATION_LABELS, POPULATION_STYLES,`. Puis, après `SourceBadge` :

```tsx
export function PopulationBadge({ population }: { population: string }) {
  // 'chr' = défaut discret ; 'architecte' = teinte indigo distinctive.
  const cls = POPULATION_STYLES[population] ?? POPULATION_STYLES.chr;
  return (
    <span
      className={`inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ring-1 ring-inset ${cls}`}
      title={population === "architecte" ? "Prescripteur (architecte d'intérieur)" : "Établissement CHR"}
    >
      {POPULATION_LABELS[population] ?? population}
    </span>
  );
}
```

`frontend/app/opportunities/page.tsx` — (1) importer `PopulationBadge` (à côté de `SourceBadge`) et `POPULATION_LABELS` si besoin ; (2) ajouter un filtre population après le filtre source (ligne ~225) :

```tsx
            <select className={SELECT_CLS} value={filters.population ?? ""} onChange={(e) => set({ population: e.target.value })}>
              <option value="">Toutes les populations</option>
              <option value="chr">CHR</option>
              <option value="architecte">Architectes</option>
            </select>
```

et (3) afficher le badge dans la cellule établissement, à côté de `<SourceBadge source={o.source} />` :

```tsx
                          <SourceBadge source={o.source} />
                          <PopulationBadge population={o.population} />
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_population.py -q` → PASS (8 tests).
Run: `python -m pytest tests/ -q` → tout vert (colonne neuve, `'chr'` partout ; aucun test CHR ne la lit ; le contournement ne se déclenche que si `population == "architecte"` ; le dashboard filtre `'chr'` par défaut donc son comportement actuel — toutes les fiches étant `'chr'` — est inchangé).

- [ ] **Step 5: Commit**

```bash
git add backend/app/models.py backend/app/database.py backend/app/ingestion/base.py backend/app/ingestion/pipeline.py backend/app/schemas.py backend/app/routes/opportunities.py backend/app/routes/dashboard.py backend/app/main.py backend/tests/test_population.py frontend/lib/types.ts frontend/lib/api.ts frontend/lib/labels.ts frontend/components/Badges.tsx frontend/app/opportunities/page.tsx
git commit -m "feat(architectes): colonne population (Opportunity, migration, LeadCandidate) + contournement du classifieur CHR + dashboard/API/UI filtre"
```

---

### Task 2: `discover_prescripteurs` + hashtags archi (découverte nationale, sans filtre CHR/IdF)

**Modèle d'exécution recommandé : sonnet**

**Files:**
- Modify: `backend/app/ingestion/instagram.py` (`ARCHI_HASHTAGS`, `PRESCRIBER_KEYWORDS`, `discover_prescripteurs`)
- Create: `backend/tests/test_discover_prescripteurs.py`

**Interfaces:**
- `ARCHI_HASHTAGS: List[str]` = les 3 hashtags mesurés par la sonde (`architectedinterieur`, `architecturedinterieure`, `agencement`) + 2 variantes proches sûres (`architecteinterieur`, `architectedinterieurparis`). `agencement` est le plus large (≈50 % d'artisans dans la sonde) : ces artisans sont capturés à la découverte MAIS écartés au garde/juge (T3) — la découverte reste volontairement large, le tri de précision est en aval.
- `PRESCRIBER_KEYWORDS: Tuple[str, ...]` (normalisés, sans accent) : formes À espace (`architecte d'interieur`, `architecture interieure`, `interior design`, `interior architect`, `designer d'interieur`, `design d'interieur`, `decoration d'interieur`, `agencement`, `studio d'architecture`, `architecte dinterieur`) **ET** formes CONTIGUËS des hashtags composés (`architectedinterieur`, `architecteinterieur`, `architecturedinterieure`, `decorationdinterieur`, `interiordesign`, `designdinterieur`). La sonde montre que les 2 hashtags les plus productifs sont contigus (`#architectedinterieur`, `#architecturedinterieure`) : les mots-clés À espace seuls les rateraient.
- `discover_prescripteurs(posts) -> List[Dict[str, str]]` : posts bruts Apify → `[{handle, name, city, type, caption, population}]`, dédupliqués par handle. **PUR** (testable). Garde un post si (1) ses `hashtags` **intersectent `ARCHI_HASHTAGS`** (le compte a été découvert PAR ce hashtag → on le garde même sans phrase en clair, ce qui rattrape les hashtags composés) **OU** (2) `_post_text(post)` (nom + caption + hashtags + location) contient un `PRESCRIBER_KEYWORD`. **AUCUN** filtre CHR (`_is_chr`) ni IdF (`_is_idf`) — national, VOLUME MAX. `type="architecte d'intérieur"`, `population="architecte"`.
- Réutilise `_norm`, `_post_text`, `_city_from_location` (existants). N'ajoute NI ne modifie AUCUNE fonction CHR (`discover` reste intact).

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_discover_prescripteurs.py
"""Tests de la découverte prescripteurs (A1, T2) — PURE, sans réseau. Grounded sur
les handles réels de la sonde (atelier_jdp, atelierlesimple, endora.studio3d…)."""
from app.ingestion.instagram import discover_prescripteurs


def _post(handle, name="", caption="", hashtags=(), location="Paris, France"):
    return {"ownerUsername": handle, "ownerFullName": name, "caption": caption,
            "hashtags": list(hashtags), "locationName": location}


def test_keeps_interior_architect_by_bio_keyword():
    out = discover_prescripteurs([
        _post("atelier_jdp", "Juliette de Poncins, architecte d'intérieur",
              "Projet Bargue", ("architectedinterieur",), "Paris"),
    ])
    assert len(out) == 1
    c = out[0]
    assert c["handle"] == "atelier_jdp"
    assert c["population"] == "architecte"
    assert c["type"] == "architecte d'intérieur"
    assert c["caption"]  # caption conservée pour le juge


def test_keeps_agencement_even_artisan_discovery_is_broad():
    # atelierlesimple (menuiserie) est capté par #agencement à la DÉCOUVERTE :
    # volontaire (large). Le garde/juge (T3) l'écartera en hors_cible.
    out = discover_prescripteurs([
        _post("atelierlesimple", "Menuiserie Atelier Lesimple",
              "Lambris en chêne", ("agencement",), "Charly"),
    ])
    assert [c["handle"] for c in out] == ["atelierlesimple"]


def test_no_idf_no_chr_filter_national_volume():
    # Compte hors IdF (Pays de la Loire) + AUCUN mot CHR : gardé quand même
    # (national, VOLUME MAX). discover() CHR l'aurait écarté.
    out = discover_prescripteurs([
        _post("espacesprojets", "Atelier Espaces & Projets",
              "Aménagement bureaux sur mesure", ("agencement",), "Château-Gontier"),
    ])
    assert [c["handle"] for c in out] == ["espacesprojets"]
    assert out[0]["city"] == "Château-Gontier"


def test_keeps_compound_hashtag_without_plaintext_phrase():
    # Piège sonde : #architectedinterieur (composé, sans espace) est le tag le plus
    # productif. Un post SANS aucune phrase archi en clair (nom + caption anodins)
    # mais portant ce hashtag doit être retenu — le compte a été découvert PAR ce
    # tag. Les mots-clés à espace seuls le rateraient.
    out = discover_prescripteurs([
        _post("bifur.architecture", "Bifur", "Projet livré",
              ("architectedinterieur",), "Nantes"),
    ])
    assert [c["handle"] for c in out] == ["bifur.architecture"]


def test_drops_unrelated_and_dedupes():
    out = discover_prescripteurs([
        _post("fitcoach", "Coach sportif", "workout", ("fitness",), "Lyon"),
        _post("atelier_jdp", "archi", "1", ("architecturedinterieure",)),
        _post("atelier_jdp", "archi", "2", ("architectedinterieur",)),  # doublon
    ])
    assert [c["handle"] for c in out] == ["atelier_jdp"]  # fitcoach écarté, dédup


def test_empty_handle_skipped():
    out = discover_prescripteurs([_post("", "archi d'intérieur", "x", ("interiordesign",))])
    assert out == []


def test_archi_hashtags_present():
    from app.ingestion.instagram import ARCHI_HASHTAGS
    for h in ("architectedinterieur", "architecturedinterieure", "agencement"):
        assert h in ARCHI_HASHTAGS
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_discover_prescripteurs.py -q`
Expected: FAIL — `ImportError: cannot import name 'discover_prescripteurs'` / `ARCHI_HASHTAGS`.

- [ ] **Step 3: Write the implementation**

Dans `backend/app/ingestion/instagram.py`, après la constante `DEFAULT_HASHTAGS` (CHR, inchangée), ajouter :

```python
# Hashtags ARCHITECTES (A1). Mesurés par la sonde (sonde-architectes.json) :
# architectedinterieur 70 %, architecturedinterieure 70 %, agencement 80 % de
# comptes distincts ; recouvrement inter-tags quasi nul -> les combiner multiplie
# la couverture. `agencement` est le plus large (~50 % d'artisans dans la sonde) :
# ratissé large à la découverte, le garde/juge trie la précision en aval.
ARCHI_HASHTAGS = [
    "architectedinterieur", "architecturedinterieure", "agencement",
    "architecteinterieur", "architectedinterieurparis",
]

# Mots-clés PRESCRIPTEUR (normalisés, sans accent) : auto-déclaration en bio/nom
# d'un métier d'architecture d'intérieur / d'agencement. Volontairement large
# (VOLUME MAX national) — le garde/juge écarte ensuite artisans, coachs, comptes
# perso. AUCUN filtre CHR ni IdF n'est appliqué (les hashtags archi sont nationaux).
# On inclut les formes À espace ET les formes CONTIGUËS des hashtags composés :
# la sonde montre que #architectedinterieur / #architecturedinterieure (contigus,
# sans espace) sont les tags les plus productifs et que les mots-clés à espace
# seuls ne les captent PAS (« architecte dinterieur » n'est pas dans
# « architectedinterieur »).
PRESCRIBER_KEYWORDS = (
    "architecte d'interieur", "architecte dinterieur", "architecture interieure",
    "interior design", "interior architect", "designer d'interieur",
    "design d'interieur", "decoration d'interieur", "agencement",
    "studio d'architecture",
    # Formes contiguës (hashtags composés) — cf. sonde.
    "architectedinterieur", "architecteinterieur", "architecturedinterieure",
    "decorationdinterieur", "interiordesign", "designdinterieur",
)
```

Puis, après la fonction `discover` (CHR, inchangée), ajouter :

```python
def _is_prescripteur(post: Dict[str, Any]) -> bool:
    """Vrai si le post révèle un métier d'archi d'intérieur / agencement (large).
    Deux voies : (1) ses `hashtags` intersectent ARCHI_HASHTAGS — le compte a été
    DÉCOUVERT par ce hashtag, on le garde même sans phrase en clair (rattrape les
    hashtags COMPOSÉS, contigus, que les mots-clés à espace ratent) ; (2) le texte
    (nom / caption / hashtags / lieu) contient un mot-clé prescripteur."""
    tags = {_norm(h) for h in (post.get("hashtags") or [])}
    if tags & {_norm(h) for h in ARCHI_HASHTAGS}:
        return True
    t = _norm(_post_text(post))
    return any(kw in t for kw in (_norm(k) for k in PRESCRIBER_KEYWORDS))


def discover_prescripteurs(posts: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Posts bruts Apify -> [{handle, name, city, type, caption, population}] :
    architectes d'intérieur (auto-déclaration en bio/nom), dédupliqués par handle.
    Fonction PURE (testable). MIROIR de discover() mais SANS filtre CHR ni IdF :
    population 'architecte', découverte NATIONALE et VOLUME MAX (décision produit).
    La précision (artisan, coach, compte perso) est traitée en aval par
    prescriber_guards + judge_prescripteur, jamais ici."""
    seen: set = set()
    out: List[Dict[str, str]] = []
    for post in posts:
        handle = (post.get("ownerUsername") or "").strip()
        if not handle or handle in seen:
            continue
        if not _is_prescripteur(post):
            continue
        seen.add(handle)
        out.append({
            "handle": handle,
            "name": (post.get("ownerFullName") or handle).strip(),
            "city": _city_from_location(post.get("locationName") or ""),
            "type": "architecte d'intérieur",
            "caption": (post.get("caption") or "")[:300],  # pour le juge
            "population": "architecte",
        })
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_discover_prescripteurs.py -q` → PASS (7 tests).
Run: `python -m pytest tests/ -q` → tout vert (fonctions purement additives ; `discover` CHR inchangé).

- [ ] **Step 5: Commit**

```bash
git add backend/app/ingestion/instagram.py backend/tests/test_discover_prescripteurs.py
git commit -m "feat(architectes): discover_prescripteurs + hashtags archi (decouverte nationale, sans filtre CHR/IdF)"
```

---

### Task 3: Gardes prescripteurs + juge `judge_prescripteur` + `classify_prescripteurs` + scoring de tier

**Modèle d'exécution recommandé : opus**

**Files:**
- Create: `backend/app/ingestion/prescriber_guards.py`
- Modify: `backend/app/ingestion/instagram.py` (`_PRESCRIBER_SYSTEM`, `judge_prescripteur`, `classify_prescripteurs`)
- Modify: `backend/app/services/scoring.py` (familles/bonus de tier prescripteur — ADDITIF)
- Modify: `backend/app/ingestion/verdict_cache.py` (fenêtres de revisite des labels archi)
- Create: `backend/tests/test_prescriber_guards.py`
- Create: `backend/tests/test_judge_prescripteur.py`
- Create: `backend/tests/test_classify_prescripteurs.py`

**Interfaces:**
- `prescriber_guards.guard_prescripteur(profile, today=None) -> Optional[str]` : verdict déterministe `"hors_cible"` | `"noise"` | `None` (→ juge). Helpers PURS : `_has_formation_cue(profile)`, `_has_artisan_metier(profile)`, `_has_archi_title(profile)`, `_is_foreign(profile)`, `_is_dead_account(profile)`. **AUCUN `studio_actif`/`studio_dormant` déterministe** (leçon sonde : le titre seul ne suffit pas).
- `judge_prescripteur(client, handle, name, profile, caption=None, match_result=None, today=None) -> Dict[str, Any]` : appel LLM **unitaire**, MÊME infra que `judge_dossier` (`OPENAI_JUDGE_MODEL`, `response_format` JSON, `temperature=0`, fail-soft `{}`). Dossier avec âges/cadence PRÉCALCULÉS en code (`_age_label` + comptage 90 j). Sortie `{reasoning, label, confidence, hospitality_proof, addresses, emails}` ; `label ∈ {studio_actif, studio_dormant, compte_perso, hors_cible}`. Prompt en français.
- `classify_prescripteurs(candidates, profiles, *, tagged_studios=None, match_fn=None, client=_USE_ENV, today=None) -> List[Dict[str, Any]]` : pour chaque candidat — garde → sinon pré-enrichissement + matcher (CHR-gated, `None` pour les archis) + juge → **fail-soft** (`client=None` ou juge `{}` → `label="studio_actif"`, `confidence="basse"`, NON caché en aval, protège le VOLUME). Calcule `tier` : `T1` si `handle ∈ tagged_studios` ; `T2` si `studio_actif` ET `hospitality_proof` ; `T3` si `studio_actif` ; sinon `None`. Annote `label`, `tier`, `confidence`, `hospitality_proof`, `_match`, enrichissement (adresse/email/site). Sans DB (injectable).
- `scoring.py` (ADDITIF, CHR intact) : `PRESCRIBER_HOT = {"projet CHR détecté"}` (+3), `PRESCRIBER_WARM = {"portfolio hospitality/CHR"}` (+2), et `"prescripteur actif"`/`"studio en sommeil"` NEUTRES (famille `"prescripteur"`, aucun bonus). Les leads CHR n'émettent jamais ces libellés → scores CHR bit-à-bit identiques.
- `verdict_cache.REVISIT_MONTHS` (ADDITIF) : `hors_cible` +12, `compte_perso` +12, `studio_dormant` +6, `studio_actif` +2 (re-visite fréquente pour capter le booster « nouveau projet »). Les clés CHR sont INCHANGÉES.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_prescriber_guards.py
"""Gardes déterministes prescripteurs (A1, T3). Grounded sur les 4 hors_cible de
la sonde : endora (coach/cours), habiteretgrandir (coach), atelierlesimple
(menuiserie), cotefauteuils (tapissier)."""
from datetime import date

from app.ingestion.prescriber_guards import (
    guard_prescripteur, _has_formation_cue, _has_artisan_metier,
    _has_archi_title, _is_dead_account,
)

TODAY = date(2026, 7, 10)


def test_formation_coach_is_hors_cible():
    # habiteretgrandir : « coach HOMER® » même avec titre archi -> hors_cible.
    prof = {"biography": "Architecte d'intérieur HOMER® / +400 plans en tant que coach HOMER®",
            "postsCount": 447, "followersCount": 743}
    assert _has_formation_cue(prof)
    assert guard_prescripteur(prof, TODAY) == "hors_cible"


def test_cours_prives_is_hors_cible():
    # endora.studio3d : vend des cours privés SketchUp AUX archis (B2B2B).
    prof = {"biography": "Collaboration & Cours privés SketchUp. 3D pour les architectes d'intérieur",
            "postsCount": 49, "followersCount": 187}
    assert guard_prescripteur(prof, TODAY) == "hors_cible"


def test_artisan_without_archi_title_is_hors_cible():
    # atelierlesimple : menuiserie/ébénisterie, PAS d'architecte -> hors_cible.
    prof = {"biography": "Menuiserie & Ébénisterie depuis 1892. Atelier à Charly (18)",
            "fullName": "Menuiserie Atelier Lesimple", "postsCount": 72, "followersCount": 335}
    assert _has_artisan_metier(prof) and not _has_archi_title(prof)
    assert guard_prescripteur(prof, TODAY) == "hors_cible"


def test_tapissier_is_hors_cible():
    prof = {"biography": "Artisan Tapissier Décorateur. Réfections Fauteuils",
            "fullName": "Côté Fauteuils", "postsCount": 30, "followersCount": 200}
    assert guard_prescripteur(prof, TODAY) == "hors_cible"


def test_artisan_WITH_archi_title_passes_to_judge():
    # Un studio archi qui mentionne « menuiserie sur-mesure » NE doit PAS être
    # écarté (titre archi présent) -> None (juge).
    prof = {"fullName": "Juliette de Poncins, architecte d'intérieur",
            "biography": "Interior designer based in Paris. Menuiserie sur-mesure.",
            "postsCount": 132, "followersCount": 681}
    assert _has_archi_title(prof)
    assert guard_prescripteur(prof, TODAY) is None


def test_studio_actif_is_NEVER_deterministic():
    # Titre archi + portfolio : la sonde impose de NE PAS trancher au garde
    # (divnaanni a le titre mais est compte_perso). -> None (juge décide).
    prof = {"fullName": "Atelier du Large", "biography": "Architectures & Intérieurs. Nous concevons des lieux justes.",
            "postsCount": 40, "followersCount": 500}
    assert guard_prescripteur(prof, TODAY) is None


def test_non_prescriber_photographer_is_hors_cible():
    prof = {"biography": "Photographe culinaire, création de contenu pour restaurants",
            "postsCount": 100, "followersCount": 2000}
    assert guard_prescripteur(prof, TODAY) == "hors_cible"


def test_foreign_domain_is_hors_cible():
    prof = {"biography": "Architecte d'intérieur à Bruxelles", "externalUrl": "https://studio.be",
            "postsCount": 50, "followersCount": 300}
    assert guard_prescripteur(prof, TODAY) == "hors_cible"


def test_dead_account_is_noise():
    prof = {"biography": "", "postsCount": 1, "followersCount": 3}
    assert _is_dead_account(prof)
    assert guard_prescripteur(prof, TODAY) == "noise"
```

```python
# backend/tests/test_judge_prescripteur.py
"""Juge prescripteur unitaire (A1, T3) — sans réseau (client factice)."""
from datetime import date

from app.ingestion.instagram import judge_prescripteur

TODAY = date(2026, 7, 10)


class _FakeCompletion:
    def __init__(self, content):
        self.choices = [type("C", (), {"message": type("M", (), {"content": content})()})()]


class _FakeClient:
    def __init__(self, content):
        self._content = content
        self.last_messages = None
        outer = self

        class _Completions:
            def create(self, **kwargs):
                outer.last_messages = kwargs.get("messages")
                return _FakeCompletion(outer._content)

        self.chat = type("Chat", (), {"completions": _Completions()})()


PROFILE = {
    "postsCount": 132, "followersCount": 681, "businessCategoryName": None,
    "biography": "Interior designer based in Paris",
    "externalUrl": "http://juliettedeponcins.com/",
    "latestPosts": [
        {"timestamp": "2026-07-10T10:00:00.000Z", "caption": "Projet Bargue — banquette sur-mesure"},
        {"timestamp": "2026-07-02T10:00:00.000Z", "caption": "Atmosphère chaleureuse"},
    ],
}


def test_returns_parsed_label_and_hospitality():
    client = _FakeClient('{"reasoning": "portfolio actif", "label": "studio_actif", '
                         '"confidence": "haute", "hospitality_proof": true, '
                         '"addresses": [], "emails": ["contact@jdp.com"]}')
    out = judge_prescripteur(client, "atelier_jdp", "Juliette", PROFILE, today=TODAY)
    assert out["label"] == "studio_actif"
    assert out["hospitality_proof"] is True
    assert out["emails"] == ["contact@jdp.com"]


def test_prompt_has_date_anchor_precomputed_recency_and_reasoning():
    client = _FakeClient('{"reasoning": "x", "label": "studio_dormant", "confidence": "moyenne", '
                         '"hospitality_proof": false, "addresses": [], "emails": []}')
    judge_prescripteur(client, "x", "X", PROFILE, today=TODAY)
    joined = " ".join(m["content"] for m in client.last_messages)
    assert "Date du jour : 2026-07-10" in joined
    assert '"reasoning"' in joined                 # reasoning exigé avant le label
    assert "dernier post" in joined.lower()         # récence précalculée présente
    assert "2026-07-10T10" not in joined            # timestamp brut jamais donné au LLM
    # Espace de labels prescripteurs présent dans le format de sortie.
    assert "studio_actif" in joined and "compte_perso" in joined and "hors_cible" in joined


def test_fail_soft():
    assert judge_prescripteur(None, "x", "X", PROFILE, today=TODAY) == {}
    assert judge_prescripteur(_FakeClient("pas du json"), "x", "X", PROFILE, today=TODAY) == {}
```

```python
# backend/tests/test_classify_prescripteurs.py
"""classify_prescripteurs : garde -> matcher -> juge -> tiering (A1, T3). Sans réseau."""
from datetime import date

from app.ingestion.instagram import classify_prescripteurs

TODAY = date(2026, 7, 10)


class _FakeCompletion:
    def __init__(self, content):
        self.choices = [type("C", (), {"message": type("M", (), {"content": content})()})()]


class _FakeClient:
    def __init__(self, content):
        class _Completions:
            def create(_self, **kwargs):
                return _FakeCompletion(content)
        self.chat = type("Chat", (), {"completions": _Completions()})()


ACTIF = ('{"reasoning": "x", "label": "studio_actif", "confidence": "haute", '
         '"hospitality_proof": %s, "addresses": [], "emails": []}')


def _cand(handle, name="Studio"):
    return {"handle": handle, "name": name, "city": "Paris",
            "type": "architecte d'intérieur", "caption": "", "population": "architecte"}


def test_guard_hors_cible_short_circuits_without_llm():
    prof = {"biography": "Menuiserie & Ébénisterie", "fullName": "Menuiserie",
            "postsCount": 72, "followersCount": 335}
    out = classify_prescripteurs([_cand("menuis")], {"menuis": prof},
                                 client=None, match_fn=None, today=TODAY)
    assert out[0]["label"] == "hors_cible" and out[0]["tier"] is None


def test_fail_soft_keeps_as_studio_actif_basse():
    prof = {"biography": "Architecte d'intérieur", "postsCount": 40, "followersCount": 300,
            "latestPosts": [{"timestamp": "2026-07-01T10:00:00.000Z", "caption": "Projet"}]}
    out = classify_prescripteurs([_cand("archi")], {"archi": prof},
                                 client=None, match_fn=None, today=TODAY)
    assert out[0]["label"] == "studio_actif" and out[0]["confidence"] == "basse"
    assert out[0]["tier"] == "T3"  # actif sans preuve hospitality -> T3


def test_tier_t2_when_hospitality_proof():
    prof = {"biography": "Architecte d'intérieur", "postsCount": 40, "followersCount": 300,
            "latestPosts": [{"timestamp": "2026-07-01T10:00:00.000Z", "caption": "Hôtel"}]}
    client = _FakeClient(ACTIF % "true")
    out = classify_prescripteurs([_cand("archi")], {"archi": prof},
                                 client=client, match_fn=None, today=TODAY)
    assert out[0]["label"] == "studio_actif" and out[0]["tier"] == "T2"


def test_tier_t1_when_tagged_on_detected_chr_project():
    prof = {"biography": "Architecte d'intérieur", "postsCount": 40, "followersCount": 300,
            "latestPosts": [{"timestamp": "2026-07-01T10:00:00.000Z", "caption": "Projet"}]}
    client = _FakeClient(ACTIF % "false")
    out = classify_prescripteurs([_cand("atelierdularge")], {"atelierdularge": prof},
                                 client=client, match_fn=None,
                                 tagged_studios={"atelierdularge"}, today=TODAY)
    assert out[0]["tier"] == "T1"  # T1 domine T2/T3


def test_dormant_has_no_tier():
    prof = {"biography": "Architecte d'intérieur", "postsCount": 447, "followersCount": 16000,
            "latestPosts": [{"timestamp": "2025-10-25T10:00:00.000Z", "caption": "..."}]}
    client = _FakeClient('{"reasoning":"vieux","label":"studio_dormant","confidence":"moyenne",'
                         '"hospitality_proof":false,"addresses":[],"emails":[]}')
    out = classify_prescripteurs([_cand("dormant")], {"dormant": prof},
                                 client=client, match_fn=None, today=TODAY)
    assert out[0]["label"] == "studio_dormant" and out[0]["tier"] is None
```

Ajouter à un test de scoring existant OU créer `backend/tests/test_scoring_prescripteur.py` (non-régression CHR + tiers) :

```python
# backend/tests/test_scoring_prescripteur.py
"""Scoring des leads prescripteurs (A1, T3) — additif, CHR intact."""
from datetime import date

from app.services.scoring import compute_score

TODAY = date(2026, 7, 10)
D = date(2026, 7, 5)  # signal récent


def _score(main, secondary):
    return compute_score(main, secondary, D, ["prescription luminaires", "sourcing"],
                         None, "instagram", today=TODAY).score


def test_neutral_prescriber_scores_low():
    # 'prescripteur actif' seul : aucun bonus de nature (score bas).
    s = _score("prescripteur actif", [])
    assert s <= 5


def test_t1_outranks_t2_outranks_t3():
    t3 = _score("prescripteur actif", [])
    t2 = _score("prescripteur actif", ["portfolio hospitality/CHR"])
    t1 = _score("prescripteur actif", ["projet CHR détecté"])
    assert t1 > t2 > t3


def test_chr_scores_unchanged_by_prescriber_addition():
    # Non-régression : un lead CHR 'ouverture prochaine' n'émet aucun libellé
    # prescripteur -> score inchangé (bonus ouverture +3 + fraîcheur).
    from app.services.scoring import OPENING_SIGNALS
    assert "ouverture prochaine" in OPENING_SIGNALS
    s = compute_score("ouverture prochaine", [], D, ["luminaires", "mobilier"],
                      None, "instagram", today=TODAY).score
    assert s >= 5  # inchangé vs comportement historique
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_prescriber_guards.py tests/test_judge_prescripteur.py tests/test_classify_prescripteurs.py tests/test_scoring_prescripteur.py -q`
Expected: FAIL — `ModuleNotFoundError: prescriber_guards` ; `ImportError` (`judge_prescripteur`/`classify_prescripteurs`) ; `KeyError`/`AttributeError` (familles `PRESCRIBER_HOT`/`PRESCRIBER_WARM` absentes).

- [ ] **Step 3: Write the implementation**

**a) Créer `backend/app/ingestion/prescriber_guards.py`** :

```python
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
Cas ancrés hors_cible : endora.studio3d (cours privés), habiteretgrandir (coach),
atelierlesimple (menuiserie), cotefauteuils (tapissier)."""
from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Any, Dict, Optional

# Formation / coaching VERS d'autres pros (B2B2B) : le compte vend du savoir, pas
# des projets clients. Grounded : endora (« cours privés »), habiteretgrandir
# (« coach HOMER® »). Word-boundary pour éviter « transformation » -> « formation ».
_FORMATION_KW = ("coach", "coaching", "cours prive", "cours prives", "formation",
                 "masterclass", "mentorat", "e-learning", "e learning", "apprendre le")

# Métiers d'artisan / fabricant VOISINS (fournisseurs, pas prescripteurs). Grounded :
# atelierlesimple (menuiserie/ébénisterie), cotefauteuils (tapissier).
_ARTISAN_KW = ("menuiserie", "menuisier", "ebenisterie", "ebeniste", "tapissier",
               "tapisserie", "serrurier", "marbrier", "ferronnier",
               "fabricant de meubles", "fabrication de meubles")

# Titre archi/design d'intérieur : sa présence NEUTRALISE le garde artisan (un
# studio qui parle de « menuiserie sur-mesure » n'est pas un menuisier).
_ARCHI_TITLE_KW = ("architecte d'interieur", "architecte dinterieur",
                   "architectes d'interieur", "architecture interieure",
                   "interior design", "interior architect", "designer d'interieur",
                   "design d'interieur")

# Prestataire de contenu / média / non-lieu (pas un studio d'archi).
_NON_PRESCRIBER_KW = ("graphiste", "webdesign", "web design", "ux/ui", "ux ui",
                      "community manager", "photographe", "webmagazine",
                      "motion design", "illustrateur")

# Domaines étrangers (piège CHR connu ; garde léger, aucun cas dans l'échantillon).
_FOREIGN_TLD = (".be", ".ch", ".ca", ".lu")


def _norm(text: Optional[str]) -> str:
    text = (text or "").lower()
    return "".join(c for c in unicodedata.normalize("NFD", text)
                   if unicodedata.category(c) != "Mn")


def _haystack(profile: Dict[str, Any]) -> str:
    """Bio + nom + catégorie business, normalisés (sans accent)."""
    return _norm(" \n ".join([
        profile.get("biography") or "", profile.get("fullName") or "",
        profile.get("businessCategoryName") or "",
    ]))


def _kw_present(hay: str, keywords) -> bool:
    """Mot-clé présent en frontière de mot (évite les sous-chaînes parasites)."""
    return any(re.search(r"(?<![a-z])" + re.escape(k) + r"(?![a-z])", hay) for k in keywords)


def _has_formation_cue(profile: Dict[str, Any]) -> bool:
    return _kw_present(_haystack(profile), _FORMATION_KW)


def _has_archi_title(profile: Dict[str, Any]) -> bool:
    return _kw_present(_haystack(profile), _ARCHI_TITLE_KW)


def _has_artisan_metier(profile: Dict[str, Any]) -> bool:
    return _kw_present(_haystack(profile), _ARTISAN_KW)


def _is_non_prescriber(profile: Dict[str, Any]) -> bool:
    return _kw_present(_haystack(profile), _NON_PRESCRIBER_KW)


def _is_foreign(profile: Dict[str, Any]) -> bool:
    urls = [profile.get("externalUrl") or ""]
    urls += [(e.get("url") or "") for e in (profile.get("externalUrls") or [])]
    hay = " ".join(urls).lower()
    return any(tld in hay for tld in _FOREIGN_TLD)


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
    Ordre : compte mort -> noise ; formation/coaching -> hors_cible (même avec titre
    archi : un coach vend du savoir) ; prestataire/média -> hors_cible ; artisan
    SANS titre archi -> hors_cible ; étranger -> hors_cible ; sinon None (le juge
    tranche actif/dormant/perso, avec récence/cadence précalculées).
    AUCUN verdict studio_* déterministe (leçon sonde : titre insuffisant)."""
    today = today or date.today()
    if _is_dead_account(profile):
        return "noise"
    if _has_formation_cue(profile):
        return "hors_cible"
    if _is_non_prescriber(profile):
        return "hors_cible"
    if _has_artisan_metier(profile) and not _has_archi_title(profile):
        return "hors_cible"
    if _is_foreign(profile):
        return "hors_cible"
    return None
```

**b) `instagram.py`** — après `judge_dossier` (CHR, inchangé), ajouter le prompt, le juge et la classification prescripteurs. Importer le module de gardes en tête (à côté de `from . import profile_guards`) :

```python
from . import profile_guards
from . import prescriber_guards
```

Puis, après `judge_dossier` :

```python
_PRESCRIBER_SYSTEM = (
    "Tu classes UN compte Instagram d'ARCHITECTE D'INTÉRIEUR / studio d'agencement "
    "en France, pour un fournisseur B2B de luminaires et mobilier qui cherche des "
    "PRESCRIPTEURS (studios qui recommandent ses produits à leurs clients). On te "
    "donne un dossier : bio, compteurs, catégorie, site/lien, âge du DERNIER post "
    "et cadence récente (déjà calculés), légende de découverte, derniers posts "
    "datés. Choisis UN label :\n"
    "- studio_actif : studio ou indépendant d'architecture d'intérieur / "
    "d'agencement, EN FRANCE, avec un portfolio ACTIF (posts récents de projets — "
    "« Projet X », conception, rénovation, sur-mesure — cadence régulière). Site "
    "propre ou email à domaine propre = signal fort.\n"
    "- studio_dormant : studio d'archi d'intérieur RÉEL mais INACTIF (dernier post "
    "ancien — plusieurs mois — ou cadence quasi nulle). Ne pas se fier au seul "
    "volume total de posts / d'abonnés : un gros compte peut être en sommeil.\n"
    "- compte_perso : passionné de déco / particulier, bio à la 1re personne "
    "orientée « mon univers / mon intérieur », email @gmail sans domaine propre, "
    "cadence irrégulière, PAS de portfolio de projets clients.\n"
    "- hors_cible : coach/formation VERS d'autres archis, artisan/fabricant "
    "(menuisier, ébéniste, tapissier), graphiste/webdesign/photographe/média, "
    "marque de produits, architecte de BÂTIMENT / maîtrise d'œuvre gros œuvre SANS "
    "aménagement intérieur, ou compte ÉTRANGER (ville/‑domaine hors France).\n"
    "RÈGLES : le titre « architecte d'intérieur » en bio est un signal FORT mais "
    "PAS suffisant seul — croise-le avec la RÉCENCE (dernier post), la cadence, le "
    "ton (portfolio de projets vs vie perso) et les signaux pro (site/email à "
    "domaine propre vs @gmail/linktr.ee). Un compte à email @gmail, cadence "
    "irrégulière et ton « mon univers » = compte_perso même avec le titre. Un "
    "« coach »/« cours privés » = hors_cible même avec le titre. En cas de doute "
    "entre studio_actif et studio_dormant, regarde l'âge du DERNIER post fourni "
    "(récent -> actif ; vieux de plusieurs mois -> dormant).\n"
    "hospitality_proof (booléen) : true SI le portfolio (bio/posts) montre un "
    "projet d'HOSPITALITY / RETAIL / CHR (hôtel, restaurant, café, bar, boutique, "
    "commerce, espace d'accueil du public) — un studio qui prescrit déjà pour ce "
    "secteur est prioritaire ; sinon false. Raisonne D'ABORD brièvement (2 phrases "
    ": activité/récence, cible du portfolio) PUIS décide. Extrais aussi, "
    "UNIQUEMENT depuis la bio/les posts de CE compte, addresses (adresses postales "
    "complètes) et emails. Réponds STRICTEMENT en JSON."
)


def _recent_cadence(profile: Dict[str, Any], today: date, window_days: int = 90) -> int:
    """Nombre de posts (parmi les ~12 derniers) datés des `window_days` derniers
    jours. Calculé EN CODE (les petits LLM ratent l'arithmétique de dates)."""
    from datetime import datetime
    n = 0
    for x in (profile.get("latestPosts") or [])[:12]:
        ts = (x.get("timestamp") or "")[:10]
        try:
            d = datetime.strptime(ts, "%Y-%m-%d").date()
        except ValueError:
            continue
        if 0 <= (today - d).days <= window_days:
            n += 1
    return n


def judge_prescripteur(client, handle: str, name: Optional[str],
                       profile: Dict[str, Any], caption: Optional[str] = None,
                       match_result=None, today: Optional[date] = None) -> Dict[str, Any]:
    """Juge prescripteur UNITAIRE : un appel LLM par compte. Renvoie {reasoning,
    label, confidence, hospitality_proof, addresses, emails} ou {} (fail-soft :
    pas de client / erreur / JSON invalide). Récence et cadence PRÉCALCULÉES en
    code (jamais de timestamp brut à soustraire par le LLM) — MÊME infra que
    judge_dossier (OPENAI_JUDGE_MODEL)."""
    if client is None:
        return {}
    today = today or date.today()
    latest = profile.get("latestPosts") or []
    # Âge du post le plus récent (récence = discriminant actif/dormant).
    newest = None
    for x in latest:
        ts = (x.get("timestamp") or "")[:10]
        if ts and (newest is None or ts > newest):
            newest = ts
    recency = _age_label(newest, today) if newest else "?"
    cadence = _recent_cadence(profile, today)
    posts_block = "\n".join(
        f'  - {_age_label((x.get("timestamp") or "")[:10], today)} : '
        f'{(x.get("caption") or "")[:160]}'
        for x in latest[:10]
    )
    site = profile.get("externalUrl") or ""
    for e in (profile.get("externalUrls") or []):
        site = site or (e.get("url") or "")
    block = (
        f'@{handle} | {name} | posts={profile.get("postsCount")} '
        f'| abonnés={profile.get("followersCount")} '
        f'| catégorie={profile.get("businessCategoryName")}\n'
        f'bio : {(profile.get("biography") or "")[:250]}\n'
        f'site/lien : {site[:120] or "(aucun)"}\n'
        f'dernier post : {recency} | posts sur 90 jours : {cadence}\n'
        f'légende de découverte : {(caption or "")[:160]}\n'
        f'derniers posts (âge daté) :\n{posts_block or "  (aucun)"}'
    )
    user = (
        f"Date du jour : {today.isoformat()}\n"
        f"Dossier :\n{block}\n\n"
        'Format EXACT : {"reasoning":"<2 phrases max>","label":"studio_actif|'
        'studio_dormant|compte_perso|hors_cible","confidence":"haute|moyenne|basse",'
        '"hospitality_proof":true|false,"addresses":[],"emails":[]}'
    )
    try:
        completion = client.chat.completions.create(
            model=os.getenv("OPENAI_JUDGE_MODEL", "gpt-4o"),
            messages=[{"role": "system", "content": _PRESCRIBER_SYSTEM},
                      {"role": "user", "content": user}],
            response_format={"type": "json_object"},
            temperature=0,
        )
        return json.loads(completion.choices[0].message.content)
    except Exception:
        return {}


def classify_prescripteurs(
    candidates: List[Dict[str, Any]],
    profiles: Optional[Dict[str, Dict[str, Any]]] = None,
    *, tagged_studios: Optional[set] = None, match_fn=None,
    client=_USE_ENV, today: Optional[date] = None,
) -> List[Dict[str, Any]]:
    """Étiquette CHAQUE candidat archi d'un label prescripteur + un tier de
    priorité. Chaîne : gardes déterministes (prescriber_guards) -> sinon matcher
    SIRET (CHR-gated -> None pour les archis en A1, mais appelé « tel quel ») ->
    juge unitaire (judge_prescripteur). Fail-soft (pas de client / juge {}) ->
    studio_actif confiance basse (gardé, protège le VOLUME ; NON caché en aval).
    Tiering (studio_actif uniquement) : T1 si handle ∈ tagged_studios (studio tagué
    sur un chantier CHR détecté) ; T2 si hospitality_proof ; sinon T3.
    Fonction sans DB (match_fn/client/tagged_studios injectables)."""
    if not candidates:
        return candidates
    today = today or date.today()
    profiles = profiles or {}
    tagged = {t.lower() for t in (tagged_studios or set())}
    resolved_client = _openai_client() if client is _USE_ENV else client

    out: List[Dict[str, Any]] = []
    for c in candidates:
        prof = profiles.get(c["handle"].lower()) or {}
        has_data = bool(prof.get("latestPosts") or prof.get("postsCount") is not None)

        # 1. Gardes déterministes (hors_cible / noise), gratuits.
        guard = prescriber_guards.guard_prescripteur(prof, today) if has_data else None
        if guard:
            c["label"] = guard
            c["confidence"] = "haute"
            c["tier"] = None
            c["hospitality_proof"] = False
            c["_match"] = None
            out.append(c)
            continue

        # 2. Pré-enrichissement + matcher SIRET (appelé tel quel ; CHR-gated ->
        # None pour un NAF archi -> le juge travaille sur le profil seul en A1).
        struct_addr = _struct_address(prof)
        struct_city = _clean_city((prof.get("businessAddress") or {}).get("city_name"))
        if struct_addr:
            c["address"] = struct_addr
        c["bio_snippet"] = (prof.get("biography") or "")[:300]
        match = match_fn(c) if match_fn else None
        c["_match"] = match

        # 3. Juge unitaire (fail-soft : studio_actif basse, gardé).
        verdict = (judge_prescripteur(resolved_client, c["handle"], c.get("name"), prof,
                                      caption=c.get("caption"), match_result=match, today=today)
                   if (resolved_client and has_data) else {})
        c["label"] = verdict.get("label") or "studio_actif"
        c["confidence"] = verdict.get("confidence") or ("basse" if not verdict else "moyenne")
        c["hospitality_proof"] = bool(verdict.get("hospitality_proof"))

        # 4. Tiering (studio_actif seulement).
        if c["label"] == "studio_actif":
            if c["handle"].lower() in tagged:
                c["tier"] = "T1"          # tagué sur un chantier CHR détecté
            elif c["hospitality_proof"]:
                c["tier"] = "T2"          # preuve hospitality/CHR au portfolio
            else:
                c["tier"] = "T3"          # studio actif générique
        else:
            c["tier"] = None

        # 5. Post-enrichissement (adresses/emails/site).
        llm_addrs = [a for a in (verdict.get("addresses") or []) if a]
        llm_emails = [e for e in (normalize_email(e) for e in (verdict.get("emails") or [])) if e]
        biz_email = normalize_email(prof.get("businessEmail") or prof.get("public_email"))
        addresses = ([struct_addr] if struct_addr else []) + [a for a in llm_addrs if a != struct_addr]
        emails = ([biz_email] if biz_email else []) + [e for e in llm_emails if e != biz_email]
        if addresses:
            c["address"] = addresses[0]
            c["extra_addresses"] = addresses[1:]
        if struct_city:
            c["city"] = struct_city
        if emails:
            c["email"] = emails[0]
            c["extra_emails"] = emails[1:]
        website = _external_url(prof)
        if website:
            c["website"] = website
        out.append(c)
    return out
```

**c) `scoring.py`** — ADDITIF (CHR intact). Après `INVENTORY_SIGNALS` :

```python
INVENTORY_SIGNALS = {"établissement en activité", "extension multi-sites"}
# Population ARCHITECTES (A1). 'prescripteur actif' / 'studio en sommeil' = NEUTRES
# (aucun bonus de nature -> score bas, VOLUME). Les libellés de TIER apportent la
# priorité : 'projet CHR détecté' (T1, studio tagué sur un chantier CHR détecté)
# = +3 (accroche « j'ai vu votre projet X ») ; 'portfolio hospitality/CHR' (T2)
# = +2. Ces libellés ne sont JAMAIS émis par un lead CHR -> scores CHR inchangés.
PRESCRIBER_NEUTRAL = {"prescripteur actif", "studio en sommeil"}
PRESCRIBER_HOT = {"projet CHR détecté"}
PRESCRIBER_WARM = {"portfolio hospitality/CHR"}
```

Étendre `SIGNAL_FAMILY` (chaque nouveau libellé sa famille) :

```python
SIGNAL_FAMILY = {
    **{s: "opening" for s in OPENING_SIGNALS},
    **{s: "takeover" for s in TAKEOVER_SIGNALS},
    **{s: "renovation" for s in RENOVATION_SIGNALS},
    **{s: "recruitment" for s in RECRUITMENT_SIGNALS},
    **{s: "inventaire" for s in INVENTORY_SIGNALS},
    **{s: "prescripteur" for s in PRESCRIBER_NEUTRAL},
    **{s: "prescripteur_hot" for s in PRESCRIBER_HOT},
    **{s: "prescripteur_warm" for s in PRESCRIBER_WARM},
}
```

Dans `compute_score`, après le bloc `RECRUITMENT_SIGNALS` (avant « Signaux croisés »), ajouter :

```python
    if all_signals & RECRUITMENT_SIGNALS:
        points += 2
        reasons.append("recrutement actif")
    # Population architectes (A1) : priorité par TIER (le main_signal 'prescripteur
    # actif' reste neutre ; ce sont les libellés de tier qui portent la priorité).
    if all_signals & PRESCRIBER_HOT:
        points += 3
        reasons.append("studio tagué sur un projet CHR détecté")
    if all_signals & PRESCRIBER_WARM:
        points += 2
        reasons.append("portfolio hospitality/CHR")
```

**d) `verdict_cache.py`** — ADDITIF. Étendre `REVISIT_MONTHS` (clés CHR inchangées) :

```python
REVISIT_MONTHS = {
    "not_venue": 12,
    "established": 6,
    "chain_multisite": 6,
    "noise": 2,
    "unknown": 2,
    # Population architectes (A1) : hors_cible/compte_perso longtemps en sommeil ;
    # studio_dormant 6 mois ; studio_actif re-visité souvent (2 mois) pour capter
    # le booster « nouveau projet » (tier T1).
    "hors_cible": 12,
    "compte_perso": 12,
    "studio_dormant": 6,
    "studio_actif": 2,
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_prescriber_guards.py tests/test_judge_prescripteur.py tests/test_classify_prescripteurs.py tests/test_scoring_prescripteur.py -q` → PASS.
Run: `python -m pytest tests/ -q` → tout vert. **Vérifier CHR intact** : `python -m pytest tests/test_scoring.py tests/test_verdict_cache.py tests/test_funnel_v2.py -q` → inchangés.

- [ ] **Step 5: Commit**

```bash
git add backend/app/ingestion/prescriber_guards.py backend/app/ingestion/instagram.py backend/app/services/scoring.py backend/app/ingestion/verdict_cache.py backend/tests/test_prescriber_guards.py backend/tests/test_judge_prescripteur.py backend/tests/test_classify_prescripteurs.py backend/tests/test_scoring_prescripteur.py
git commit -m "feat(architectes): gardes + juge prescripteur + classify_prescripteurs + scoring de tier (additif, CHR intact)"
```

---

### Task 4: Extraction des studios tagués (tier T1) + `run_prescripteurs` recâblé

**Modèle d'exécution recommandé : opus**

**Files:**
- Modify: `backend/app/ingestion/instagram.py` (`extract_tagged_studios`)
- Modify: `backend/app/ingestion/pipeline.py` (`PRESCRIBER_ROUTING`, `_build_tagged_studios`, `run_prescripteurs`, imports)
- Create: `backend/tests/test_run_prescripteurs.py`

**Interfaces:**
- `extract_tagged_studios(profiles) -> Set[str]` (instagram.py) : scanne les légendes des `latestPosts` de profils CHR (dict `{handle: profil}`) pour les mentions `@studio`, renvoie l'ensemble des handles mentionnés (minuscules, sans le `@`). PUR. C'est la matérialisation du tier T1 : un studio archi tagué dans les posts de chantier d'un lead CHR détecté par la machine.
- `pipeline.PRESCRIBER_ROUTING: Dict[str, Tuple[str, List[str], str]]` — `{label: (main_signal, secondary_base, lifecycle_label)}` pour `studio_actif`/`studio_dormant` (les tiers T1/T2 ajoutent leur libellé au moment du routage). `compte_perso`/`hors_cible`/`noise` **absents** → cache seul, pas de lead.
- `pipeline._build_tagged_studios(session, scrape_fn=scrape_profiles, limit=200) -> Set[str]` : récupère les handles Instagram des leads CHR (`population='chr'`, `source='instagram'`), scrape leurs profils (borné), extrait les `@mentions`. Injectable (`scrape_fn`) pour les tests. Fail-soft (`set()` si rien).
- `run_prescripteurs(hashtags=None, limit=40, session=None, posts=None, tagged_studios=None) -> IngestStats` : MIROIR de `run_instagram` — `discover_prescripteurs` → `should_rejudge` (cache partagé) → `scrape_profiles` → `classify_prescripteurs(tagged_studios=…, match_fn=_match_result)` → `verdict_cache.upsert` (mêmes règles de cacheabilité que CHR : un `studio_actif` confiance `basse` fail-soft N'EST PAS caché) → `LeadCandidate(population='architecte')` routé par `PRESCRIBER_ROUTING` → `_process_candidate`. `tagged_studios=None` → construit via `_build_tagged_studios(session)`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_run_prescripteurs.py
"""run_prescripteurs recâblé (A1, T4) — sans réseau ni LLM réels."""
from datetime import date

from sqlmodel import Session, SQLModel, create_engine, select

import app.ingestion.instagram as ig
import app.ingestion.pipeline as pl
from app.ingestion.instagram import extract_tagged_studios
from app.models import HandleVerdict, Opportunity


class _FakeCompletion:
    def __init__(self, content):
        self.choices = [type("C", (), {"message": type("M", (), {"content": content})()})()]


class _FakeClient:
    def __init__(self, content):
        class _Completions:
            def create(_self, **kwargs):
                return _FakeCompletion(content)
        self.chat = type("Chat", (), {"completions": _Completions()})()


def _engine(tmp_path):
    e = create_engine(f"sqlite:///{tmp_path/'t.db'}")
    SQLModel.metadata.create_all(e)
    return e


def _post(handle, caption="Projet d'archi", hashtags=("architectedinterieur",)):
    return {"ownerUsername": handle, "ownerFullName": handle, "caption": caption,
            "hashtags": list(hashtags), "locationName": "Paris"}


def _prep(monkeypatch, profiles, judge_json=None, tagged=None):
    monkeypatch.setattr(pl, "scrape_profiles", lambda handles, **k: profiles)
    monkeypatch.setattr(pl, "match_siret", lambda **kw: None)
    # Pas de tagged auto : injecté explicitement (évite un 2e scrape en test).
    if judge_json is None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    else:
        monkeypatch.setenv("OPENAI_API_KEY", "x")
        monkeypatch.setattr(ig, "_openai_client", lambda: _FakeClient(judge_json))


def test_extract_tagged_studios_pure():
    profiles = {
        "resto1": {"latestPosts": [
            {"caption": "Merci @atelierdularge pour le design ! @non_studio"},
            {"caption": "Ambiance signée @bifur.architecture"}]},
    }
    tags = extract_tagged_studios(profiles)
    assert "atelierdularge" in tags and "bifur.architecture" in tags
    assert "non_studio" in tags  # extraction brute ; le filtrage se fait au match handle


def test_hors_cible_no_lead_but_cached(tmp_path, monkeypatch):
    prof = {"biography": "Menuiserie & Ébénisterie", "fullName": "Menuiserie",
            "postsCount": 72, "followersCount": 335,
            "latestPosts": [{"timestamp": "2026-07-01T10:00:00.000Z"}]}
    _prep(monkeypatch, {"menuis": prof})
    with Session(_engine(tmp_path)) as s:
        pl.run_prescripteurs(posts=[_post("menuis", "menuiserie", ("agencement",))],
                             session=s, tagged_studios=set())
        s.commit()
        assert s.exec(select(Opportunity)).all() == []  # pas de lead
        verdicts = {v.handle: v.verdict for v in s.exec(select(HandleVerdict)).all()}
        assert verdicts.get("menuis") == "hors_cible"     # mais verdict caché


def test_studio_actif_becomes_architect_lead(tmp_path, monkeypatch):
    prof = {"biography": "Architecte d'intérieur à Paris", "postsCount": 40, "followersCount": 500,
            "latestPosts": [{"timestamp": "2026-07-05T10:00:00.000Z", "caption": "Projet"}]}
    _prep(monkeypatch, {"studioa": prof},
          judge_json='{"reasoning":"x","label":"studio_actif","confidence":"haute",'
                     '"hospitality_proof":false,"addresses":[],"emails":[]}')
    with Session(_engine(tmp_path)) as s:
        pl.run_prescripteurs(posts=[_post("studioa")], session=s, tagged_studios=set())
        s.commit()
        opp = s.exec(select(Opportunity).where(Opportunity.source_ref == "studioa")).first()
        assert opp is not None
        assert opp.population == "architecte"
        assert opp.establishment_type == "architecte d'intérieur"
        assert opp.main_signal == "prescripteur actif"
        assert opp.lifecycle_label == "studio_actif"


def test_t1_tagged_studio_gets_hot_secondary(tmp_path, monkeypatch):
    prof = {"biography": "Architecte d'intérieur", "postsCount": 40, "followersCount": 500,
            "latestPosts": [{"timestamp": "2026-07-05T10:00:00.000Z", "caption": "Projet"}]}
    _prep(monkeypatch, {"atelierdularge": prof},
          judge_json='{"reasoning":"x","label":"studio_actif","confidence":"haute",'
                     '"hospitality_proof":false,"addresses":[],"emails":[]}')
    with Session(_engine(tmp_path)) as s:
        pl.run_prescripteurs(posts=[_post("atelierdularge")], session=s,
                             tagged_studios={"atelierdularge"})
        s.commit()
        opp = s.exec(select(Opportunity).where(Opportunity.source_ref == "atelierdularge")).first()
        assert "projet CHR détecté" in (opp.secondary_signals or [])
        # T1 doit scorer plus haut qu'un T3 générique.
        assert opp.opportunity_score >= 5


def test_fail_soft_studio_actif_basse_not_cached(tmp_path, monkeypatch):
    prof = {"biography": "Architecte d'intérieur", "postsCount": 40, "followersCount": 500,
            "latestPosts": [{"timestamp": "2026-07-05T10:00:00.000Z", "caption": "Projet"}]}
    _prep(monkeypatch, {"douteux": prof})  # pas de juge -> studio_actif basse
    with Session(_engine(tmp_path)) as s:
        pl.run_prescripteurs(posts=[_post("douteux")], session=s, tagged_studios=set())
        s.commit()
        opp = s.exec(select(Opportunity).where(Opportunity.source_ref == "douteux")).first()
        assert opp is not None and opp.lifecycle_label == "studio_actif"
        verdicts = {v.handle for v in s.exec(select(HandleVerdict)).all()}
        assert "douteux" not in verdicts  # basse fail-soft : non caché (re-jugé au prochain run)


def test_build_tagged_studios_from_chr_leads(tmp_path, monkeypatch):
    from app.ingestion.base import LeadCandidate
    from app.ingestion.pipeline import IngestStats, _process_candidate, _build_tagged_studios
    with Session(_engine(tmp_path)) as s:
        # Un lead CHR Instagram existant.
        _process_candidate(s, LeadCandidate(source="instagram", source_ref="resto1",
                           establishment_name="Resto", city="Paris", address="",
                           main_signal="ouverture prochaine", detection_date=date(2026, 7, 1),
                           establishment_type="restaurant", instagram="resto1"),
                           IngestStats(source="instagram"), set(), enricher=None)
        s.commit()
        fake_scrape = lambda handles, **k: {"resto1": {"latestPosts": [
            {"caption": "design @atelierdularge"}]}}
        tags = _build_tagged_studios(s, scrape_fn=fake_scrape)
        assert "atelierdularge" in tags
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_run_prescripteurs.py -q`
Expected: FAIL — `ImportError` (`extract_tagged_studios`) ; `AttributeError` (`run_prescripteurs`/`_build_tagged_studios`/`PRESCRIBER_ROUTING` absents).

- [ ] **Step 3: Write the implementation**

**a) `instagram.py`** — après `discover_prescripteurs`, ajouter :

```python
_MENTION_RE = re.compile(r"@([a-zA-Z0-9_.]+)")


def extract_tagged_studios(profiles: Dict[str, Dict[str, Any]]) -> "set":
    """Scanne les légendes des derniers posts de profils CHR pour les mentions
    @compte -> ensemble de handles mentionnés (minuscules, sans @). PUR.
    Matérialise le tier T1 : un studio archi tagué dans les posts de chantier d'un
    lead CHR détecté par la machine (« j'ai vu votre projet X »). Le filtrage
    (est-ce vraiment un studio archi ?) se fait ensuite par intersection avec les
    handles découverts en population archi — on n'infère rien ici."""
    tagged: set = set()
    for prof in (profiles or {}).values():
        for x in (prof.get("latestPosts") or []):
            for m in _MENTION_RE.findall(x.get("caption") or ""):
                tagged.add(m.lower())
    return tagged
```

**b) `pipeline.py`** — importer les nouvelles fonctions (compléter la ligne d'import instagram) :

```python
from .instagram import (
    discover, scrape_hashtags, scrape_profiles, classify_profiles,
    discover_prescripteurs, classify_prescripteurs, extract_tagged_studios,
)
```

Ajouter `Set` et `Tuple` aux imports typing en tête :

```python
from typing import List, Optional, Set, Tuple
```

Après `LABEL_ROUTING` (CHR, inchangé), ajouter le routage prescripteurs :

```python
# Signal NEUTRE des leads PRESCRIPTEURS (A1) : hors familles de scoring CHR
# (services/scoring.py) -> score naturellement bas ; ce sont les libellés de tier
# (projet CHR détecté / portfolio hospitality/CHR) qui portent la priorité.
PRESCRIBER_SIGNAL = "prescripteur actif"
DORMANT_SECONDARY = "studio en sommeil"
T1_SECONDARY = "projet CHR détecté"
T2_SECONDARY = "portfolio hospitality/CHR"

# Routage label prescripteur -> (main_signal, secondary_base, lifecycle_label).
# compte_perso/hors_cible/noise ABSENTS -> cache seul (pas de lead). Les tiers
# T1/T2 ajoutent leur libellé secondaire au moment du routage (cf. run_prescripteurs).
PRESCRIBER_ROUTING = {
    "studio_actif":   (PRESCRIBER_SIGNAL, [],                   "studio_actif"),
    "studio_dormant": (PRESCRIBER_SIGNAL, [DORMANT_SECONDARY],  "studio_dormant"),
}
```

Après `run_instagram` (inchangé), ajouter le constructeur de tags et le run miroir :

```python
def _build_tagged_studios(session: Session, scrape_fn=scrape_profiles,
                          limit: int = 200) -> Set[str]:
    """Ensemble des @comptes tagués dans les posts des leads CHR Instagram
    existants (matérialise le tier T1). Scrape borné (limit) des profils CHR, puis
    extract_tagged_studios. Fail-soft : set() si aucun lead / pas de token.
    `scrape_fn` injectable pour les tests."""
    handles = [
        o.instagram for o in session.exec(
            select(Opportunity).where(
                Opportunity.source == "instagram",
                Opportunity.population == "chr",
                Opportunity.instagram.is_not(None),
            )
        ).all()[:limit]
        if o.instagram
    ]
    if not handles:
        return set()
    profiles = scrape_fn(handles) or {}
    return extract_tagged_studios(profiles)


def run_prescripteurs(
    hashtags: Optional[List[str]] = None,
    limit: int = 40,
    session: Optional[Session] = None,
    posts: Optional[List[dict]] = None,
    tagged_studios: Optional[Set[str]] = None,
) -> IngestStats:
    """Population ARCHITECTES (A1) : MIROIR de run_instagram, sans filtre CHR/IdF.
    Apify (hashtags archi) -> discover_prescripteurs -> cache -> scrape_profiles ->
    classify_prescripteurs (gardes/juge prescripteur/tiering) -> upsert cache ->
    LeadCandidate(population='architecte') routé par PRESCRIBER_ROUTING. Le matcher
    SIRET est appelé « tel quel » (CHR-gated -> None pour les archis en A1)."""
    init_db()
    own_session = session is None
    session = session or Session(engine)
    stats = IngestStats(source="instagram", mode="prescripteurs")
    enricher = SireneEnricher()

    try:
        raw_posts = posts if posts is not None else scrape_hashtags(
            hashtags or _archi_hashtags(), limit)
        candidates = discover_prescripteurs(raw_posts)
        due = [c for c in candidates if verdict_cache.should_rejudge(session, c["handle"])]
        profiles = scrape_profiles([c["handle"] for c in due]) if due else {}
        if tagged_studios is None:
            tagged_studios = _build_tagged_studios(session)
        today = date.today()
        labeled = classify_prescripteurs(due, profiles, tagged_studios=tagged_studios,
                                         match_fn=_match_result, today=today)
        stats.fetched = len(labeled)
        seen_refs: set = set()
        for c in labeled:
            prof = profiles.get(c["handle"].lower()) or {}
            has_data = bool(prof.get("latestPosts") or prof.get("postsCount") is not None)
            # Mêmes règles de cacheabilité que CHR : un studio_actif 'basse'
            # fail-soft (scrape/juge KO) N'EST PAS caché -> re-jugé au prochain run.
            cacheable = has_data and not (
                c["label"] == "studio_actif" and (c.get("confidence") or "basse") == "basse"
            )
            routing = PRESCRIBER_ROUTING.get(c["label"])
            try:
                if cacheable:
                    verdict_cache.upsert(session, c["handle"], c["label"],
                                         c.get("confidence"), prof, today=today)
                if routing is not None:
                    main_signal, secondary_base, lifecycle_label = routing
                    secondary = list(secondary_base)
                    if c.get("tier") == "T1":
                        secondary.append(T1_SECONDARY)
                    elif c.get("tier") == "T2":
                        secondary.append(T2_SECONDARY)
                    m = c.get("_match")
                    proof_text, proof_url = _instagram_proof(c, prof)
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
                        secondary_signals=secondary,
                        lifecycle_label=lifecycle_label,
                        population="architecte",
                        detection_date=today,
                        classification_text=c["name"],
                        establishment_type="architecte d'intérieur",
                        instagram=c["handle"],
                        proof_text=proof_text or "",
                        proof_url=proof_url,
                    )
                    _process_candidate(session, cand, stats, seen_refs, enricher)
                session.commit()
            except Exception:
                stats.errors += 1
                session.rollback()
        session.commit()
    finally:
        if own_session:
            session.close()

    return stats


def _archi_hashtags() -> List[str]:
    """Hashtags archi par défaut (importés paresseusement pour éviter un cycle)."""
    from .instagram import ARCHI_HASHTAGS
    return ARCHI_HASHTAGS
```

> **Cohérence** : `_instagram_proof(c, prof)` est réutilisé tel quel (il lit `c["label"]`/`c["confidence"]` — les labels prescripteurs s'affichent proprement dans la preuve). `_process_candidate` reçoit `population='architecte'` → branche de contournement CHR de la T1. `match_siret` est monkeypatché à `None` dans les tests ; en réel il est CHR-gated et renvoie `None` pour un archi (aucun crash).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_run_prescripteurs.py -q` → PASS (6 tests).
Run: `python -m pytest tests/ -q` → tout vert.

- [ ] **Step 5: Commit**

```bash
git add backend/app/ingestion/instagram.py backend/app/ingestion/pipeline.py backend/tests/test_run_prescripteurs.py
git commit -m "feat(architectes): extraction des studios tagues (tier T1) + run_prescripteurs recable"
```

---

### Task 5: Éval architectes — mini jeu de preuve + métriques dédiées (gates)

**Modèle d'exécution recommandé : opus**

**Files:**
- Create: `backend/app/ingestion/eval/architectes_groundtruth.csv` (seed = 15 comptes classés par la sonde)
- Create: `backend/app/ingestion/eval/snapshots_architectes/` (copie des 15 profils bruts de la sonde)
- Create: `backend/app/ingestion/eval/prescripteurs_metrics.py`
- Create: `backend/app/ingestion/eval/prescripteurs_run.py`
- Create: `backend/tests/test_prescripteurs_eval.py`

**Interfaces:**
- CSV **SÉPARÉ** (l'éval CHR reste intacte). Colonnes : `handle,name,label,confidence,provenance,rationale,annotated_at`. `label ∈ {studio_actif, studio_dormant, compte_perso, hors_cible}`. Seed = les 15 comptes de la sonde (9 studio_actif, 1 compte_perso `divnaanni`, 4 hors_cible `atelierlesimple`/`cotefauteuils`/`endora.studio3d`/`habiteretgrandir`, 1 studio_dormant `helene.gombert`).
- `prescripteurs_metrics.py` (PUR, testé sur jeu jouet) : `studio_actif_precision(pairs) -> (float|None, tp, n)` (vrais studio_actif parmi les prédits studio_actif) ; `hors_cible_in_tiers(rows) -> List[str]` (handles dont la vérité = hors_cible mais rangés en T1/T2 par la prédiction — doit être VIDE) ; `label_confusion(pairs)`.
- `prescripteurs_run.py` : `run_prescripteurs_eval(strict=False) -> dict` — classe les snapshots via `classify_prescripteurs` (client=`_USE_ENV`, LLM live au gate) et calcule les gates : `GATE_STUDIO_PRECISION = 0.70` (précision `studio_actif` ≥ 70 %) et `GATE_ZERO_HORS_CIBLE_IN_TIERS` (0 hors_cible vrai rangé en T1/T2). CLI `python -m app.ingestion.eval.prescripteurs_run`.
- **Les tests unitaires (T5) NE lancent PAS le LLM** : ils testent `prescripteurs_metrics.py` sur des paires jouet + valident le CSV/snapshots présents + que les 4 hors_cible de la sonde sont bien tranchés déterministement par les gardes (offline, `client=None`). Le gate LLM live est en T6.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_prescripteurs_eval.py
"""Éval prescripteurs (A1, T5) — métriques PURES + gardes offline. Pas de LLM."""
import csv
from datetime import date
from pathlib import Path

from app.ingestion.eval.prescripteurs_metrics import (
    studio_actif_precision, hors_cible_in_tiers,
)
from app.ingestion.instagram import classify_prescripteurs

ROOT = Path(__file__).resolve().parents[1] / "app" / "ingestion" / "eval"
CSV = ROOT / "architectes_groundtruth.csv"
SNAP = ROOT / "snapshots_architectes"
TODAY = date(2026, 7, 10)


def test_groundtruth_csv_seeded():
    rows = list(csv.DictReader(CSV.open(encoding="utf-8")))
    handles = {r["handle"] for r in rows}
    # Seed sonde : au moins les 4 hors_cible + le compte_perso + le dormant.
    for h in ("atelierlesimple", "cotefauteuils", "endora.studio3d", "habiteretgrandir",
              "divnaanni", "helene.gombert", "atelier_jdp"):
        assert h in handles, f"{h} absent du CSV"
    labels = {r["label"] for r in rows}
    assert labels <= {"studio_actif", "studio_dormant", "compte_perso", "hors_cible"}


def test_studio_actif_precision_metric():
    pairs = [("studio_actif", "studio_actif"), ("compte_perso", "studio_actif"),
             ("studio_actif", "studio_dormant")]
    prec, tp, n = studio_actif_precision(pairs)
    assert (tp, n) == (1, 2) and abs(prec - 0.5) < 1e-9


def test_hors_cible_in_tiers_detects_violation():
    rows = [{"handle": "a", "true_label": "hors_cible", "tier": "T2"},
            {"handle": "b", "true_label": "studio_actif", "tier": "T1"}]
    assert hors_cible_in_tiers(rows) == ["a"]
    assert hors_cible_in_tiers([{"handle": "b", "true_label": "studio_actif", "tier": "T1"}]) == []


def test_guards_catch_all_sonde_hors_cible_offline():
    # Sans LLM (client=None), les gardes déterministes doivent classer hors_cible
    # les 4 comptes hors_cible de la sonde (grounded).
    import json
    cands, profs = [], {}
    for h in ("atelierlesimple", "cotefauteuils", "endora.studio3d", "habiteretgrandir"):
        p = SNAP / f"{h}.json"
        if not p.exists():
            continue
        profs[h.lower()] = json.loads(p.read_text(encoding="utf-8"))
        cands.append({"handle": h, "name": h, "city": "", "type": "architecte d'intérieur",
                      "caption": "", "population": "architecte"})
    assert cands, "snapshots_architectes manquants"
    out = classify_prescripteurs(cands, profs, client=None, match_fn=None, today=TODAY)
    for c in out:
        assert c["label"] == "hors_cible", f'{c["handle"]} -> {c["label"]}'
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_prescripteurs_eval.py -q`
Expected: FAIL — `ModuleNotFoundError: prescripteurs_metrics` ; CSV/snapshots absents.

- [ ] **Step 3: Write the implementation**

**a)** Copier les 15 profils bruts de la sonde dans le dossier d'éval (déterminisme offline) :

```bash
mkdir -p backend/app/ingestion/eval/snapshots_architectes
cp .superpowers/sdd/sonde-architectes-profils/*.json backend/app/ingestion/eval/snapshots_architectes/
```

**b)** Créer `backend/app/ingestion/eval/architectes_groundtruth.csv` (seed sonde — rationales courtes) :

```csv
handle,name,label,confidence,provenance,rationale,annotated_at
atelier_jdp,Juliette de Poncins,studio_actif,high,sonde,"titre exact 'architecte d'interieur', site propre, 132 posts, projets nommes",2026-07-10
desmursetdesreves,Des Murs et des Reves,studio_actif,high,sonde,"duo 'architectes d'interieur', email domaine propre, 154 posts cadence hebdo",2026-07-10
atelierdularge,Atelier du Large,studio_actif,high,sonde,"agence (pluriel 'nous concevons'), site propre, businessAddress structuree, collab CHR @hotel_restaurant_locean",2026-07-10
bifur.architecture,Bifur Architecture,studio_actif,high,sonde,"email domaine propre g.drege@bifur.fr, site, 100 posts, pole BIFUR COMMERCE (retail)",2026-07-10
grangermargon,Granger Margon,studio_actif,high,sonde,"email domaine propre, ecole Camondo, 399 posts, duo nomme",2026-07-10
em.archi.design,EM Archi Design,studio_actif,high,sonde,"titre exact 'architecte d'interieur', site propre em-archidesign.fr, zone Compiegne/Oise",2026-07-10
almonainterieurs,Almona Interieurs,studio_actif,med,sonde,"businessCategory 'Home decor', 30 posts recents 'Projet X', linktr.ee seul (jeune)",2026-07-10
anartchi,Anartchi,studio_actif,med,sonde,"vocabulaire metier 'conception/suivi des travaux/sur mesure', cadence pro, petit studio jeune",2026-07-10
espacesprojets,Atelier Espaces & Projets,studio_actif,med,sonde,"'amenagement bureaux & mobilier', zone geo en bio, segment tertiaire, profil hybride studio/revendeur",2026-07-10
divnaanni,Divna Anni,compte_perso,med,sonde,"titre 'architecte d'interieur' MAIS email @gmail, cadence tres irreguliere, ton 'mon univers'",2026-07-10
helene.gombert,Helene Gombert,studio_dormant,med,sonde,"gros compte (17k abonnes, 447 posts) email pro MAIS feed incoherent, activite en pause",2026-07-10
atelierlesimple,Atelier Lesimple,hors_cible,high,sonde,"'Menuiserie & Ebenisterie depuis 1892' = artisan/fabricant, pas architecte",2026-07-10
cotefauteuils,Cote Fauteuils,hors_cible,high,sonde,"'Artisan Tapissier Decorateur' = artisan textile/mobilier, pas architecte",2026-07-10
endora.studio3d,Endora Studio 3D,hors_cible,high,sonde,"'Cours prives SketchUp pour les architectes d'interieur' = formation B2B2B",2026-07-10
habiteretgrandir,Habiter et Grandir,hors_cible,high,sonde,"'coach HOMER®' + contenu lifestyle = coaching/formation, pas studio de projets clients",2026-07-10
```

**c)** Créer `backend/app/ingestion/eval/prescripteurs_metrics.py` :

```python
"""Métriques d'éval de la classification PRESCRIPTEURS (A1) — fonctions PURES.

Entrée = paires (label_vérité, label_prédit) et/ou lignes {true_label, tier}.
Gate principal : précision de studio_actif (un studio_actif prédit EST-il un vrai
studio_actif ?). Gate dur : 0 hors_cible vrai rangé dans un tier chaud (T1/T2)."""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Optional, Tuple

Pair = Tuple[str, str]

LABEL_ORDER = ["studio_actif", "studio_dormant", "compte_perso", "hors_cible"]


def studio_actif_precision(pairs: List[Pair]) -> Tuple[Optional[float], int, int]:
    """(vrais studio_actif parmi les prédits studio_actif) / prédits studio_actif.
    -> (précision|None, vrais_positifs, total_prédits). None si aucun prédit."""
    predicted = [truth for truth, pred in pairs if pred == "studio_actif"]
    if not predicted:
        return None, 0, 0
    tp = sum(1 for truth in predicted if truth == "studio_actif")
    return tp / len(predicted), tp, len(predicted)


def hors_cible_in_tiers(rows: List[dict]) -> List[str]:
    """Handles dont la VÉRITÉ = hors_cible mais rangés en tier chaud (T1/T2).
    DOIT être vide (gate dur : 0 hors_cible en T1/T2)."""
    return [r["handle"] for r in rows
            if r.get("true_label") == "hors_cible" and r.get("tier") in ("T1", "T2")]


def label_confusion(pairs: List[Pair]) -> Dict[str, Dict[str, int]]:
    matrix: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for truth, pred in pairs:
        matrix[truth][pred] += 1
    return {t: dict(row) for t, row in matrix.items()}
```

**d)** Créer `backend/app/ingestion/eval/prescripteurs_run.py` :

```python
"""Harness d'éval de la classification PRESCRIPTEURS (A1) — CLI.

Tourne sur des snapshots figés (snapshots_architectes/<handle>.json). Reproductible,
SÉPARÉ de l'éval CHR (qui reste intacte). Le LLM (juge prescripteur) n'est appelé
QUE si OPENAI_API_KEY est présent — c'est le gate d'acceptation (T6).

  python -m app.ingestion.eval.prescripteurs_run
  python -m app.ingestion.eval.prescripteurs_run --json out.json
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

from ..instagram import classify_prescripteurs
from .prescripteurs_metrics import (
    LABEL_ORDER, hors_cible_in_tiers, label_confusion, studio_actif_precision,
)

ROOT = Path(__file__).resolve().parent
CSV_PATH = ROOT / "architectes_groundtruth.csv"
SNAP_DIR = ROOT / "snapshots_architectes"

GATE_STUDIO_PRECISION = 0.70  # précision studio_actif >= 70 %


def load_groundtruth() -> List[dict]:
    with CSV_PATH.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_snapshot(handle: str) -> Optional[dict]:
    p = SNAP_DIR / f"{handle}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def run_prescripteurs_eval(strict: bool = False, today: Optional[date] = None) -> dict:
    today = today or date.today()
    rows = load_groundtruth()
    snapshots: Dict[str, dict] = {}
    missing: List[str] = []
    for row in rows:
        h = row["handle"].strip()
        snap = load_snapshot(h)
        if snap is None:
            missing.append(h)
            continue
        snapshots[h] = snap

    cands = [{"handle": h, "name": (snap.get("fullName") or h), "city": "",
              "type": "architecte d'intérieur", "caption": "", "population": "architecte"}
             for h, snap in snapshots.items()]
    injected = {h.lower(): snap for h, snap in snapshots.items()}
    labeled = classify_prescripteurs([dict(c) for c in cands], injected,
                                     match_fn=None, today=today)
    pred_by_handle = {c["handle"]: c for c in labeled}
    truth_by_handle = {r["handle"].strip(): r["label"].strip() for r in rows}

    pairs = [(truth_by_handle[h], pred_by_handle[h]["label"]) for h in snapshots]
    prec, tp, n = studio_actif_precision(pairs)
    detail_rows = [{"handle": h, "true_label": truth_by_handle[h],
                    "predicted_label": pred_by_handle[h]["label"],
                    "tier": pred_by_handle[h].get("tier")} for h in snapshots]
    violations = hors_cible_in_tiers(detail_rows)

    gate_precision = prec is not None and prec >= GATE_STUDIO_PRECISION
    gate_tiers = len(violations) == 0
    return {
        "n": len(snapshots), "missing": missing,
        "studio_actif_precision": prec, "studio_actif_tp": tp, "studio_actif_n": n,
        "hors_cible_in_tiers": violations,
        "confusion": label_confusion(pairs),
        "gate_studio_precision": gate_precision,
        "gate_zero_hors_cible_in_tiers": gate_tiers,
        "gates_pass": gate_precision and gate_tiers,
        "rows": detail_rows,
    }


def print_report(res: dict) -> None:
    print("=" * 60)
    print("ÉVAL — classification prescripteurs (architectes, A1)")
    print("=" * 60)
    print(f"Comptes évalués : {res['n']}")
    if res["missing"]:
        print(f"Snapshots manquants : {len(res['missing'])} ({', '.join(res['missing'])})")
    p = res["studio_actif_precision"]
    pct = "n/a" if p is None else f"{p*100:.0f}%"
    print(f"** PRÉCISION studio_actif : {pct} ** ({res['studio_actif_tp']}/{res['studio_actif_n']})")
    print(f"hors_cible en T1/T2 (doit être vide) : {res['hors_cible_in_tiers']}")
    print("Matrice (vérité -> prédit) :")
    print(f"  {'vérité':<16} " + " ".join(f"{c[:9]:>10}" for c in LABEL_ORDER))
    for t in LABEL_ORDER:
        if t in res["confusion"]:
            r = res["confusion"][t]
            print(f"  {t:<16} " + " ".join(f"{r.get(c, 0):>10}" for c in LABEL_ORDER))
    ok = "OK" if res["gates_pass"] else "ÉCHEC"
    print(f"GATES : précision studio_actif>=70% = {res['gate_studio_precision']} | "
          f"0 hors_cible en T1/T2 = {res['gate_zero_hors_cible_in_tiers']} -> {ok}")
    print("=" * 60)


def main() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT.parents[2] / ".env")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="Éval classification prescripteurs (archi)")
    parser.add_argument("--json", metavar="PATH")
    args = parser.parse_args()
    res = run_prescripteurs_eval()
    print_report(res)
    if args.json:
        Path(args.json).write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    import sys
    sys.exit(0 if res["gates_pass"] else 1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_prescripteurs_eval.py -q` → PASS (4 tests). Le test `test_guards_catch_all_sonde_hors_cible_offline` confirme que les 4 hors_cible de la sonde sont tranchés SANS LLM.
Run: `python -m pytest tests/ -q` → tout vert.

- [ ] **Step 5: Commit**

```bash
git add backend/app/ingestion/eval/architectes_groundtruth.csv backend/app/ingestion/eval/snapshots_architectes backend/app/ingestion/eval/prescripteurs_metrics.py backend/app/ingestion/eval/prescripteurs_run.py backend/tests/test_prescripteurs_eval.py
git commit -m "feat(architectes): eval prescripteurs (mini-GT sonde + metriques + gates studio_actif>=70% / 0 hors_cible en tiers)"
```

---

### Task 6: Run réel borné + CLI + gate LLM live + docs + passe d'annotation navigateur

**Modèle d'exécution recommandé : sonnet**

**Files:**
- Modify: `backend/app/ingestion/run.py` (mode `prescripteurs`)
- Modify: `backend/app/main.py` (endpoint dev `run_prescripteurs`)
- Create: `docs/population-architectes-design.md` (décisions, sonde, périmètre A1)
- Modify: `C:\Users\Alexis\.claude\projects\c--Users-Alexis-Documents-Projets\memory\MEMORY.md` (index + note A1)

**Interfaces:**
- CLI : `python -m app.ingestion.run --mode prescripteurs --limit 40` → `run_prescripteurs(limit=...)`.
- Endpoint dev : `POST /api/dev/run-prescripteurs?limit=40` (miroir de `run_instagram_endpoint`).
- **Gate LLM live d'acceptation** (manuel, hors pytest — nécessite `OPENAI_API_KEY` + snapshots) : `python -m app.ingestion.eval.prescripteurs_run` doit sortir `GATES ... OK` (précision studio_actif ≥ 70 %, 0 hors_cible en T1/T2). Si échec → itérer le prompt `_PRESCRIBER_SYSTEM`/les gardes (jamais relâcher pour « faire passer »).
- **Passe d'annotation navigateur** (documentée, exécutée après le 1er run réel) : règle STRICTE — OUVRIR les posts de chaque nouveau compte (la grille anonyme ne montre pas les captions), annoter ~5 comptes supplémentaires dans `architectes_groundtruth.csv` (`provenance=annotation_browser`), re-snapshotter, relancer le gate.
- **Éval CHR intacte** (vérification finale) : `python -m app.ingestion.eval.run` et `python -m app.ingestion.eval.match_eval` inchangés (gates CHR verts, 8/9 matching).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_run_prescripteurs_cli.py
"""CLI + endpoint prescripteurs (A1, T6) — sans réseau."""
import app.ingestion.pipeline as pl


def test_cli_has_prescripteurs_mode(monkeypatch):
    import app.ingestion.run as run
    called = {}
    monkeypatch.setattr(run, "run_prescripteurs", lambda **k: called.setdefault("k", k) or _Stats())
    import sys
    monkeypatch.setattr(sys, "argv", ["run", "--mode", "prescripteurs", "--limit", "12"])
    run.main()
    assert called["k"]["limit"] == 12


class _Stats:
    source = "instagram"
    def __init__(self):
        self.truncated = False
    # stats_to_dict utilise asdict -> fournir __dataclass_fields__ ? On simplifie :


def test_run_prescripteurs_exported():
    # run_prescripteurs doit être importable depuis pipeline (contrat CLI/endpoint).
    assert hasattr(pl, "run_prescripteurs")
```

> **Note** : `stats_to_dict` attend un dataclass. Pour éviter un faux stub, remplacer le corps de `test_cli_has_prescripteurs_mode` par un vrai `IngestStats` :

```python
def test_cli_has_prescripteurs_mode(monkeypatch):
    import sys
    import app.ingestion.run as run
    from app.ingestion.pipeline import IngestStats
    called = {}

    def fake(**k):
        called["k"] = k
        return IngestStats(source="instagram", mode="prescripteurs")

    monkeypatch.setattr(run, "run_prescripteurs", fake)
    monkeypatch.setattr(sys, "argv", ["run", "--mode", "prescripteurs", "--limit", "12"])
    run.main()
    assert called["k"]["limit"] == 12
```

(Supprimer la classe `_Stats` — inutile.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_run_prescripteurs_cli.py -q`
Expected: FAIL — `run_prescripteurs` absent du choix `--mode` (KeyError/SystemExit argparse) et non importé dans `run.py`.

- [ ] **Step 3: Write the implementation**

**a) `run.py`** — importer et brancher le mode. Ajouter `run_prescripteurs` à l'import depuis `.pipeline`, ajouter `"prescripteurs"` aux `choices`, et le dispatch (après le bloc `instagram`) :

```python
    elif args.mode == "prescripteurs":
        stats = run_prescripteurs(limit=args.limit)
```

Mettre à jour la docstring du module (liste des modes) avec :

```
  prescripteurs  population architectes d'intérieur (A1) : hashtags archi -> juge prescripteur
```

et l'exemple :

```
    python -m app.ingestion.run --mode prescripteurs --limit 40
```

**b) `main.py`** — ajouter l'endpoint dev (après `run_instagram_endpoint`) :

```python
@dev_router.post("/run-prescripteurs")
def run_prescripteurs_endpoint(limit: int = 40):
    from .ingestion.pipeline import run_prescripteurs, stats_to_dict
    try:
        return stats_to_dict(run_prescripteurs(limit=limit))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc))
```

(Aligner la gestion d'erreur sur celle de `run_instagram_endpoint`.)

**c)** Créer `docs/population-architectes-design.md` — décisions produit (pivot architectes, VOLUME MAX national, tier = booster pas filtre), résumé de la sonde (3 hashtags, 8 motifs discriminants, 6 pièges), architecture (flux parallèle `run_prescripteurs`, cache/matcher/juge réutilisés), espace de labels + tiering + routage, gates d'éval, et **Hors périmètre A1** (annuaires CFAI/Houzz = A2 ; watchlist « nouveau projet » = A3 ; élargissement du NAF-gate du matcher pour enrichir les archis en SIREN = A2 ; événementiel ; messages).

**d)** Mettre à jour `MEMORY.md` : ajouter à l'index une entrée « Population architectes (A1) — plan `docs/plans/2026-07-10-population-architectes.md`, flux `run_prescripteurs` parallèle au CHR, gates studio_actif≥70 % / 0 hors_cible en tiers ».

- [ ] **Step 4: Run tests + gates**

Run: `python -m pytest tests/test_run_prescripteurs_cli.py -q` → PASS.
Run: `python -m pytest tests/ -q` → **tout vert**.
Run (non-régression CHR, obligatoire) : `python -m app.ingestion.eval.match_eval` → **8/9, 0 faux merge**. Avec `OPENAI_API_KEY` : `python -m app.ingestion.eval.run` → gates CHR verts (recall opening 4/4, hot_precision ≥ 60 %).
Gate LLM live archi (avec `OPENAI_API_KEY`) : `python -m app.ingestion.eval.prescripteurs_run` → `GATES ... OK`.
Run réel borné (avec `APIFY_TOKEN` + `OPENAI_API_KEY`, coût assumé) : `python -m app.ingestion.run --mode prescripteurs --limit 20` → vérifier des leads `population=architecte` créés, `main_signal='prescripteur actif'`, tiers cohérents dans l'UI (`?population=architecte`). Puis passe d'annotation navigateur (ouvrir les posts, annoter ~5 comptes, re-snapshot, relancer le gate archi).

- [ ] **Step 5: Commit**

```bash
git add backend/app/ingestion/run.py backend/app/main.py backend/tests/test_run_prescripteurs_cli.py docs/population-architectes-design.md
git commit -m "feat(architectes): CLI/endpoint run-prescripteurs + gate LLM live + docs + hors-perimetre A1"
```

(Le commit de `MEMORY.md` est hors dépôt `chr-signal-radar` : mettre à jour le fichier mémoire séparément, pas dans ce commit git.)

---

## Auto-relecture de cohérence inter-tâches

- **`population` (T1) → utilisée partout** : `_process_candidate` branche `is_architecte` (T1) est LA garde qui empêche le classifieur CHR de dropper les archis ; `run_prescripteurs` (T4) pose `population='architecte'` sur chaque `LeadCandidate` ; le filtre API `?population` (T1) est ce que l'UI (T1) et le run réel (T6) exploitent. Aucune tâche ne crée de lead archi avant T4 → la colonne reste `'chr'` partout jusque-là (T1 vert seul).
- **`_process_candidate` — un seul point de contournement** : la branche `is_architecte` calcule `etype` AVANT le split `existing`/création, donc couvre upsert ET création. L'enricher est sauté pour les archis (comme `source=='sirene'`), ce qui évite qu'un enrichissement pose un NAF archi qui re-dropperait le lead.
- **Labels prescripteurs réutilisent `lifecycle_label`** : T1 étend `LIFECYCLE_LABEL_LABELS/STYLES` (frontend) avec `studio_actif`/`studio_dormant` ; T4 pose `lifecycle_label=studio_actif|studio_dormant`. Pas de nouvelle colonne (au-delà de `population`). `compte_perso`/`hors_cible` ne deviennent jamais des leads → jamais affichés.
- **Scoring additif ⇒ CHR intact** : les familles `PRESCRIBER_HOT/WARM` (T3) et les libellés de tier (T4) ne sont émis QUE par des leads `population='architecte'` ; les leads CHR ne les portent jamais → `compute_score` renvoie exactement les mêmes valeurs pour le CHR. Le `main_signal 'prescripteur actif'` est neutre (aucune famille de nature). Test de non-régression explicite en T3.
- **Cache partagé, agnostique** : `HandleVerdict`/`verdict_cache` sont réutilisés tels quels ; T3 n'AJOUTE que des clés archi à `REVISIT_MONTHS` (clés CHR inchangées). Les règles de cacheabilité de `run_prescripteurs` (T4) copient exactement celles de `run_instagram` (un `studio_actif` `basse` fail-soft n'est pas caché).
- **Matcher « tel quel »** : `classify_prescripteurs` (T3) et `run_prescripteurs` (T4) appellent `match_fn=_match_result` ; le matcher est CHR-gated → renvoie `None` pour un archi sans crash. Le juge prescripteur gère `match_result=None` (registre « aucun match »). Aucune modification du matcher (préserve `match_eval` 8/9).
- **Éval séparée (T5) ⇒ éval CHR (T6 vérif) intacte** : CSV/snapshots/module dédiés ; `run.py` (CHR) et `match_eval` non touchés. T5 seed le CSV depuis la sonde ; T6 ajoute la passe navigateur.
- **Fail-soft cohérent** : sans LLM, `classify_prescripteurs` → `studio_actif`/`basse` (gardé, VOLUME, non caché) ; les 4 hors_cible de la sonde sont quand même tranchés par les gardes déterministes (testé offline en T5). Le gate de précision est LIVE (T6), comme pour le CHR.

## Hors périmètre A1 (à ne PAS implémenter ici)

- **Annuaires** (CFAI, Houzz…) comme source de découverte → brique **A2**.
- **Watchlist « nouveau projet »** (re-visite périodique des studios pour détecter un post de nouveau chantier, booster T1 dynamique) → brique **A3** (réutilisera le cache `HandleVerdict`).
- **Enrichissement SIREN des architectes** (élargir le NAF-gate du matcher au-delà du CHR : 71.11Z/74.10Z) → **A2**. En A1 le matcher reste CHR-gated (no-op propre pour les archis) et le juge travaille sur le profil seul.
- **Événementiel** (2e canal de vente historique d'Alexis) → hors A1 (focus pur archi d'intérieur).
- **Génération de messages** spécifiques prescripteurs (accroche « j'ai vu votre projet X ») → réutilise la génération existante, non spécialisée en A1.
- **Filtre géographique IdF** pour les archis → volontairement ABSENT (VOLUME MAX national, décision produit).

---

## Notes de revue

Revue de plan (2026-07-10). Deux findings **important** ont été appliqués directement dans le plan :

- **[important — appliqué, Task 2]** Le filet de découverte était aveugle aux **hashtags composés** : `_is_prescripteur` ne testait que des expressions À espace (`architecte dinterieur`), alors que les 2 tags les plus productifs de la sonde sont contigus (`#architectedinterieur`, `#architecturedinterieure`). Résultat : la découverte reposait de fait sur le seul `#agencement`, contredisant l'objectif VOLUME MAX (les tests passaient quand même car ils s'appuyaient sur un nom en clair ou sur `#agencement`). **Correction** : `discover_prescripteurs` retient désormais tout post dont les `hashtags` intersectent `ARCHI_HASHTAGS` (le compte a été découvert PAR ce tag), les formes contiguës sont ajoutées à `PRESCRIBER_KEYWORDS`, et un test prouve qu'un post `{caption:'Projet livré', hashtags:['architectedinterieur']}` sans phrase en clair est retenu.
- **[important — appliqué, Task 1]** `GET /api/dashboard/stats` (`get_stats`) faisait `select(Opportunity).all()` **sans filtre `population`** : les leads architectes (T4/T6) auraient pollué `total_opportunities`, `by_signal` (ligne « prescripteur actif ») et surtout `hottest` (un archi T1/T2 à score élevé pouvait déloger un lead CHR chaud du top 5). Le plan filtrait la liste et le meta mais avait oublié le dashboard. **Correction** : `get_stats` prend un paramètre `population` par défaut `'chr'` (compteurs/by_signal/hottest filtrés) ; `?population=architecte` cible les archis, `?population=` (vide) = toutes ; test de non-pollution ajouté.
- **[important — escalade a posteriori, Task 2, round 1]** Contradiction interne du plan constatée à l'exécution : la prose de Task 2 dit « Réutilise `_city_from_location` (existants). N'ajoute NI ne modifie AUCUNE fonction CHR (`discover` reste intact) », mais le test verbatim `test_no_idf_no_chr_filter_national_volume` exige `_city_from_location("Château-Gontier") == "Château-Gontier"`, ce que l'ancien découpage `re.split(r"[,\-]", loc)` (tout tiret) ne peut pas satisfaire — dette documentée par ailleurs (HANDOFF.md « Extraction de ville cassée »). L'exécutant a corrigé `_city_from_location` (partagée CHR + archi : ne découper que sur `,` et le tiret ESPACÉ `' - '`, jamais le tiret collé des noms composés) sans que cette décision de toucher une fonction partagée CHR soit tracée ici au moment de l'exécution (seule une mention dans le rapport de tâche, non corroborée). **Conséquence réelle, non cosmétique** : `discover()` (CHR) produit désormais, pour toute commune IdF composée à tiret (Saint-Denis, Boulogne-Billancourt, Levallois-Perret, Issy-les-Moulineaux, Neuilly-sur-Seine…), la commune ENTIÈRE au lieu du fragment tronqué d'avant (ex. 'Boulogne' au lieu de 'Boulogne-Billancourt') ; cette valeur `city` part telle quelle dans `pipeline._match_result` -> `match_siret(city=...)`, le matcher SIRET réellement utilisé par le pipeline CHR live. **Décision (round 1) : le fix est conservé** — il corrige une dette réelle et documentée, et la solution alternative (dupliquer un helper local rien que pour `discover_prescripteurs`) aurait laissé le bug CHR connu intact sans bénéfice. Ce paragraphe constitue l'escalade/documentation manquante ; un test de régression exerçant le chemin LIVE (`discover()` avec ville composée -> `_match_result` -> `match_siret`, pas seulement l'éval offline `match_eval` qui reconstruit sa ville depuis un `CITY_HINTS` codé en dur et ne peut rien détecter ici) a été ajouté (`test_discover_hyphenated_idf_city_reaches_live_matcher`, `backend/tests/test_ingestion.py`).

Trois findings **minor** n'ont **pas** été appliqués (fix non trivial ou entrelacé au récit du plan) — à trancher par l'exécutant :

- **[minor — cache non population-aware]** `verdict_cache`/`HandleVerdict` est réutilisé tel quel mais sa clé est le seul `handle`. Un handle vu par les deux funnels (possible via `#agencement`, qui ramène du retail/CHR-adjacent) peut entrer en **collision** : un verdict CHR `not_venue` (+12 mois) ferait renvoyer `False` à `should_rejudge` côté archi (compte jamais jugé prescripteur), et réciproquement un `hors_cible` archi masquerait un futur intérêt CHR. **Recommandation** : préfixer la clé du cache par la population **uniquement dans `run_prescripteurs`** (ex. `f"arch:{handle}"` aux appels `should_rejudge`/`upsert` de T4 — n'affecte PAS `run_instagram`, donc CHR bit-à-bit intact ; adapter l'assertion du test `test_hors_cible_no_lead_but_cached` en `arch:menuis`). Alternative : colonne `population` sur `HandleVerdict`. Non appliqué car cela touche la sémantique du cache partagé (et la future brique A3 qui réutilisera `HandleVerdict`) ; risque faible à volume actuel mais réel. À noter : le point de coherence « Cache partagé, agnostique » de l'auto-relecture sous-estime ce risque.
- **[minor — matcher non « no-op gratuit »]** Le matcher SIRET est décrit comme « no-op propre » pour les archis, mais `run_prescripteurs` passe `match_fn=_match_result`, qui fait une **recherche Sirene HTTP par nom+ville pour CHAQUE candidat** (voire un appel LLM arbitre) avant de renvoyer structurellement `None` (CHR-gated). Sur une découverte « VOLUME MAX national », c'est un coût réseau réel par lead, pas un no-op gratuit. **Recommandation** : court-circuiter en A1 en passant **`match_fn=None`** dans `run_prescripteurs` (le résultat est de toute façon `None` → aucune perte fonctionnelle : `establishment_name` retombe sur `c["name"]`, le juge travaille déjà sur le profil seul). Non appliqué inline car le plan présente délibérément le matcher comme « appelé tel quel / MIROIR de `run_instagram` » en plusieurs endroits (architecture, décision #7, interfaces T3/T4, auto-relecture) ; changer `match_fn` imposerait de réviser ce récit partout. Correctness inchangée dans les deux cas.
- **[minor — meta `cities` non ventilé]** `main.py:get_meta` construit `cities` depuis TOUTES les opportunités sans filtre `population`. Les leads archis étant nationaux (hors IdF par design), le sélecteur de villes du dashboard CHR se retrouve inondé de villes non-IdF. **Cosmétique** mais réel. **Recommandation** : ventiler `cities` par population (ou l'accepter). Non appliqué (cosmétique, hors périmètre du filtrage fonctionnel).
