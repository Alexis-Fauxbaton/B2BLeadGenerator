# Correctifs revue finale de branche — feature/inventaire-complet

Deux défauts « important » introduits/élargis par la brique 3bis (routage
« tout label devient un lead ») corrigés en une passe.

## 1. Incohérence lifecycle_label ↔ lifecycle_stage

**Symptôme.** Une fiche Insta « en base » (`lifecycle_label` = `established` ou
`chain_multisite`) porte `main_signal` = signal NEUTRE « établissement en
activité » et n'a côté Insta ni `review_count`, ni `venue_origin_date`, ni
`activity_start_date`. `lifecycle_stage()` tombait alors dans le repli final et
renvoyait `ouvert récemment` — l'inverse d'un établissement `établi`. L'API
sérialisait donc `lifecycle_label='established'` ET `lifecycle_stage='ouvert
récemment'` sur la même fiche (contradiction visible en UI). Avant 3bis, les
`established` Insta ne créaient aucun lead : le repli ne s'appliquait jamais à
eux.

**Correctif.**
- `backend/app/services/lifecycle.py` : `lifecycle_stage()` gagne un paramètre
  `lifecycle_label`. Un label `established`/`chain_multisite`
  (`ESTABLISHED_LABELS`) force `stage='établi'` **avant** le repli heuristique
  (âge/détection). Placé après les discriminants registre fiables (avis, origine
  du local, date d'activité) et après le court-circuit `closed` (fermé prime
  tout) ; un label `unknown` ne force rien (repli heuristique inchangé).
- `backend/app/schemas.py` : la property dérivée `lifecycle_stage` passe
  désormais `lifecycle_label=self.lifecycle_label` à `_stage(...)`.

## 2. Verdict de cache committé AVANT la création du lead

**Symptôme.** Dans `run_instagram`, le verdict de cache était `upsert` + `commit`
dans une transaction séparée, AVANT la création de la fiche. Si
`_process_candidate` échouait ensuite (enrich réseau, classify, scoring),
l'exception était avalée (`stats.errors++`, rollback) mais le verdict restait
caché. Au run suivant `should_rejudge()` renvoyait `False` toute la fenêtre de
revisite (6 mois pour established/chain, 2 mois pour unknown) → le handle n'était
plus scrapé/jugé et sa fiche « en base » n'était JAMAIS créée. La brique 3bis
élargit ce trou (avant, established/chain ne créaient pas de lead). Les leads
chauds étaient épargnés (`opening_soon`/`just_opened` = `NEVER_CACHED`).

**Correctif.**
- `backend/app/ingestion/pipeline.py` : verdict de cache et lead sont désormais
  écrits dans la **même unité transactionnelle** et committés une seule fois par
  candidat. Si la création du lead échoue, le rollback annule AUSSI le verdict →
  `should_rejudge` reste vrai, le handle est re-jugé au prochain run. Le cas
  `not_venue`/`noise` (aucun lead) reste caché seul, comme avant. L'isolation
  inter-candidats est préservée (un commit par candidat protège les couples
  verdict+lead déjà réussis du lot).

## Tests

- `backend/tests/test_ingestion.py::test_lifecycle_stage` : ajout des cas
  `established`/`chain_multisite` → `établi`, cohérence même en détection
  récente, `unknown` non forcé, `closed` prime le label.
- `backend/tests/test_inventaire_routing.py` :
  - `test_established_lead_stage_is_coherent` : bout-en-bout, une fiche
    established sérialisée expose `lifecycle_stage='établi'`.
  - `test_verdict_not_cached_when_lead_creation_fails` : `_process_candidate`
    monkeypatché en échec → ni lead, ni verdict caché, `should_rejudge` reste
    `True`.

**Suite complète** : `167 passed` (backend, `pytest -q`).

**Éval live** (funnel touché, snapshots figés + LLM) :
- Rappel opening : 5/5 = 100 % (gate ≥ 100 % ✓)
- Précision segment chaud : 75 % (gate ≥ 60 % ✓)
- GATES : OK (exit 0)

## Fichiers modifiés

- `backend/app/services/lifecycle.py`
- `backend/app/schemas.py`
- `backend/app/ingestion/pipeline.py`
- `backend/tests/test_ingestion.py`
- `backend/tests/test_inventaire_routing.py`
