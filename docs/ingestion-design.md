# Design — Pipeline d'ingestion de leads réels (BODACC)

Date : 2026-06-27

## Objectif
Prouver qu'on peut récupérer de **vrais leads CHR** depuis une source publique et
les faire apparaître dans l'app (dashboard, opportunités, pipeline), via la même
logique de scoring/canal que les données seedées.

Faisabilité validée en live : l'API BODACC (opendatasoft, sans authentification)
renvoie ~32 000 créations CHR récentes en Île-de-France, filtrables et avec une
URL de preuve par annonce.

## Source : BODACC
- Endpoint : `https://bodacc-datadila.opendatasoft.com/api/explore/v2.1/catalog/datasets/annonces-commerciales/records`
- Filtres ODSQL : `region_code=11` (Île-de-France) ou liste de départements via `numerodepartement`,
  `familleavis in (creation, vente, modification)`, plein-texte CHR, `dateparution >= since`.
- Pagination : `limit` (≤100) + `offset`, `total_count` fourni.

### Mapping
| BODACC | Opportunity |
|---|---|
| `familleavis=creation` | main_signal = `création récente` |
| `familleavis=vente` | main_signal = `reprise` |
| `modification` + `listeprecedentproprietaire` | main_signal = `changement propriétaire` |
| `commercant` / `denomination` | establishment_name |
| `ville`, `cp`, `adresseSiegeSocial` | city, address |
| `activite` (personnes/établissements) | classification du type CHR |
| `dateparution` | detection_date |
| `url_complete` | proof_url |
| résumé `jugement`/`acte`/`activite` | proof_text |
| personne physique (`pp`) | decision_maker (si présent) |

`probable_needs` et `estimated_timing` déduits du type/signal (mêmes tables qu'au seed).

## Architecture
```
backend/app/ingestion/
  base.py            # Connector (ABC) + LeadCandidate (dataclass)
  chr_classifier.py  # classify(text) -> establishment_type | None  (mots-clés)
  bodacc.py          # BodaccConnector(Connector)
  pipeline.py        # run_ingestion() : fetch → classer → enrichir → dédup → scorer → upsert
  run.py             # CLI
```
- Réutilise `services/scoring.py` et `services/channel_recommendation.py`.
- `pipeline.run_ingestion(connector, since_days, limit, departments, reset)` renvoie
  des stats : `fetched, chr_matched, created, updated, skipped_dupes, errors`.

## Modèle
Ajout à `Opportunity` :
- `source` : `"demo"` | `"bodacc"` (défaut `"demo"`).
- `source_ref` : identifiant d'annonce BODACC, sert à la **dédup/upsert**.

Migration SQLite légère dans `database.init_db()` : `ALTER TABLE ADD COLUMN` si absent.
`seed.py` ne supprime que les lignes `source="demo"` → les leads importés persistent.

## Déclenchement
- CLI : `python -m app.ingestion.run --source bodacc --since 90 --departments 75,92,93,94 --limit 100`
- API : `POST /api/dev/ingest` `{source, since_days, limit, departments}` → stats.
- UI : bouton « Importer (BODACC) » dans le header de `/opportunities`, rafraîchit la liste.

## Robustesse
- Tolérance par enregistrement (record invalide compté en `errors`, n'interrompt pas le batch).
- Retry réseau (1), cap de pages, court délai entre pages.
- Classification CHR isolée et testable (faux positifs assumés, améliorable).

## Tests
- Unitaire : `chr_classifier` (chaînes d'activité variées).
- Unitaire : mapping `bodacc` sur un fixture capturé (sans réseau).
- Smoke test live optionnel (réseau).

## Scheduling
Décision (2026-06-27) : **on-demand uniquement** pour cette itération (CLI / endpoint / bouton UI).
Pas d'orchestrateur. V2 : ingestion incrémentale planifiée (curseur sur `dateparution`)
via APScheduler in-process ou tâche planifiée Windows.

## Limites
- `activite` BODACC parfois vide → classification par mots-clés (activite + dénomination + plein-texte).
- Pas d'enrichissement SIRENE/adresse normalisée à ce stade (V2).
