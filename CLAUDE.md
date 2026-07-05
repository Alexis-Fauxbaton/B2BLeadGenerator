# CLAUDE.md — CHR Signal Radar

PoC/MVP en cours : SaaS B2B qui détecte, qualifie et suit des opportunités
commerciales dans le CHR (cafés/hôtels/restaurants) pour des fournisseurs
(fournisseur de démo : **LumaPro**, luminaires/mobilier/ambiance).

**Architecture complète (overview + deep dive)** : `docs/ARCHITECTURE.md`.

## Stack
- **Frontend** : Next.js 14 (App Router) + TypeScript + Tailwind — dossier `frontend/`
- **Backend** : FastAPI + SQLModel + SQLite — dossier `backend/`
- **IA messages** : OpenAI si `OPENAI_API_KEY` présent, sinon templates locaux

## Lancer en local
```bash
# Backend
cd backend
python -m venv .venv && .venv\Scripts\activate      # Windows
pip install -r requirements.txt
python -m app.seed                                   # données de démo
uvicorn app.main:app --port 8000 --reload            # http://localhost:8000/docs

# Frontend (autre terminal)
cd frontend
npm install
npm run dev                                           # http://localhost:3000
```
`.env` backend : `OPENAI_API_KEY=`, `DATABASE_URL=sqlite:///./chr_signal_radar.db`,
`INSEE_API_KEY=` (portail-api.insee.fr, gratuite — source `sirene`, fail-soft
sans clé).
`.env.local` frontend : `NEXT_PUBLIC_API_URL=http://localhost:8000`.

## Ingestion de leads réels (ETL) — `app/ingestion/`
- **Extract** : `bodacc.py` (API BODACC opendatasoft, sans clé) → signal/date/SIREN ;
  `sirene_delta.py` (API Sirene INSEE, clé `INSEE_API_KEY`) → délta des
  nouveaux SIRET CHR (NAF 55/56), fenêtre passée + créations pré-déclarées
  (date future au registre), fusion cross-source par SIREN avec BODACC.
- **Transform** : `enrichment/sirene.py` (via `recherche-entreprises.api.gouv.fr`,
  sans clé) → NAF/enseigne/adresse/état ; `enrichment/naf_classifier.py` (NAF
  fait autorité pour le type CHR) ; `chr_classifier.py` (repli mots-clés).
- **Load** : `pipeline.py` → scoring/canal + upsert SQLite (dédup sur `source_ref`).

Modes CLI : `python -m app.ingestion.run --mode {window|incremental|reenrich|backfill} --source {bodacc|sirene}`.
- `incremental` = nouveaux depuis le curseur ; `reenrich` = guérit `naf IS NULL`
  via SIREN stocké (Sirene-only, supprime les faux positifs confirmés) ;
  `backfill` = filet large ; détection de troncature via `total_count`.

## Conventions
- **SQLModel** : modèles dans `models.py`. Champs liste (`secondary_signals`,
  `probable_needs`) en colonnes JSON.
- **Migrations légères** : ajout de colonne via `database._run_lightweight_migrations()`
  (ALTER TABLE conditionnel) — pas d'Alembic pour l'instant.
- **Provenance** : `source` ("demo"/"bodacc"), `source_ref` (dédup), `siren`, `naf`.
- **Enrichisseurs** : fail-soft (jamais bloquant), cache + rate-limit.
- **Services réutilisés partout** : `services/scoring.py`, `services/channel_recommendation.py`.
- Frontend : liens en markdown `[texte](chemin)` ; composants dans `components/`,
  appels API dans `lib/api.ts`, types dans `lib/types.ts`, libellés dans `lib/labels.ts`.

## Tests
```bash
cd backend && python -m pytest tests/ -q
```

## Pièges connus (vécu)
- **Ne pas lancer `npm run build` pendant que `npm run dev` tourne** : conflit de
  cache `.next` (erreur "Cannot find module './xxx.js'"). Stopper le dev d'abord.
- **uvicorn doit tourner avec `--reload`** sinon les changements de routes ne sont
  pas pris en compte (bug vécu sur l'ajout du filtre `source`).
- **Console Windows = cp1252** : les `print` avec emoji/accents plantent ou
  s'affichent mal ; la base et l'API restent en UTF-8 correct.

## État & roadmap
Fait : UI complète, ingestion BODACC+Sirene avec stratégie de mise à jour.
Roadmap MVP (par priorité) : **1. contact actionnable (EN COURS)** → 2. France
Travail (signal recrutement) + fusion par SIREN → 3. scheduling + Postgres +
hébergement → 4. auth + flag de confiance. Détail : `docs/ingestion-design.md`.
