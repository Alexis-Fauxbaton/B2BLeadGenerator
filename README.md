# CHR Signal Radar — PoC

PoC local d'une app SaaS qui aide des **fournisseurs B2B** (ex. *LumaPro* :
luminaires, mobilier, solutions d'ambiance) à **détecter, qualifier et suivre**
des opportunités commerciales dans le **CHR** (cafés, hôtels, restaurants).

L'app détecte des opportunités à fort « moment d'achat » : ouverture prochaine,
reprise/cession, changement de propriétaire, rénovation, recrutement, expansion,
nouveau point de vente, signal récent… Pour chaque opportunité : fiche
établissement, signal + preuve, score d'opportunité, besoin probable, canal
recommandé, messages générés (IA ou templates), statut, historique, relance.

> ⚠️ **PoC** — données **seedées** réalistes. Aucune connexion réelle à BODACC,
> INPI, Google Maps ou France Travail. Pas d'auth, pas de paiement, pas de scraping.

---

## Stack

| Couche      | Techno                                   |
|-------------|------------------------------------------|
| Frontend    | Next.js 14 (App Router) + TypeScript     |
| UI          | Tailwind CSS + lucide-react              |
| Backend     | FastAPI                                   |
| ORM / DB    | SQLModel + SQLite                         |
| IA messages | OpenAI (optionnel) + fallback templates  |

---

## Arborescence

```
chr-signal-radar/
├── backend/
│   ├── app/
│   │   ├── main.py            # app FastAPI, CORS, routers, /api/meta, /api/dev/seed
│   │   ├── database.py        # moteur SQLite + session
│   │   ├── models.py          # Opportunity, Signal, ContactHistory, Settings
│   │   ├── schemas.py         # schémas Pydantic (I/O API)
│   │   ├── seed.py            # peuplement (python -m app.seed)
│   │   ├── routes/            # opportunities, messages, pipeline, dashboard, settings
│   │   └── services/          # scoring, channel_recommendation, message_generation, demo_data
│   ├── requirements.txt
│   └── .env.example
├── frontend/
│   ├── app/                   # /, /opportunities, /opportunities/[id], /pipeline, /settings
│   ├── components/            # Sidebar, Badges, StatCard, CopyButton, States…
│   ├── lib/                   # api.ts, types.ts, labels.ts
│   ├── package.json
│   ├── tailwind.config.ts
│   └── .env.local.example
└── README.md
```

---

## Installation & lancement

### 1. Backend (FastAPI)

```bash
cd backend
python -m venv .venv

# Windows (PowerShell)
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt

# Créer le .env (laisser OPENAI_API_KEY vide pour utiliser les templates locaux)
cp .env.example .env        # Windows: copy .env.example .env

# Peupler la base SQLite (~54 opportunités réalistes)
python -m app.seed

# Lancer l'API (http://localhost:8000, docs sur /docs)
uvicorn app.main:app --reload
```

### 2. Frontend (Next.js)

Dans un **second terminal** :

```bash
cd frontend
npm install

# Pointer vers le backend
cp .env.local.example .env.local   # Windows: copy .env.local.example .env.local

npm run dev
# → http://localhost:3000
```

---

## Variables d'environnement

**backend/.env**
```
OPENAI_API_KEY=          # vide = fallback templates locaux
OPENAI_MODEL=gpt-4o-mini
DATABASE_URL=sqlite:///./chr_signal_radar.db
```

**frontend/.env.local**
```
NEXT_PUBLIC_API_URL=http://localhost:8000
```

Si `OPENAI_API_KEY` est renseignée, les messages sont générés par OpenAI ;
sinon (ou en cas d'erreur réseau/quota) l'app retombe **automatiquement** sur
des templates locaux propres. La fiche détail indique la source utilisée.

---

## API REST

| Méthode | Endpoint                                          | Description                       |
|---------|---------------------------------------------------|-----------------------------------|
| GET     | `/api/dashboard/stats`                            | KPIs + répartitions + top 5       |
| GET     | `/api/opportunities`                              | liste filtrable/triable           |
| GET     | `/api/opportunities/{id}`                         | fiche détail complète             |
| PATCH   | `/api/opportunities/{id}`                         | mise à jour partielle             |
| PATCH   | `/api/opportunities/{id}/status`                  | changement de statut + relance    |
| POST    | `/api/opportunities/{id}/generate-messages`       | 4 variantes de messages           |
| GET     | `/api/pipeline`                                   | opportunités groupées par statut  |
| GET / PATCH | `/api/settings`                               | profil fournisseur                |
| GET     | `/api/meta`                                       | options de filtres                |
| POST    | `/api/dev/seed`                                   | re-seed via HTTP                  |

Filtres disponibles sur `/api/opportunities` : `search`, `city`,
`establishment_type`, `main_signal`, `status`, `min_score`,
`recommended_channel`, `sort_by` (`score`|`detection_date`|`city`|`status`),
`order` (`desc`|`asc`).

---

## Logique métier

- **Scoring (0–10)** — `services/scoring.py`. Combine la nature du signal
  (ouverture/reprise +3, rénovation/recrutement +2), la **fraîcheur** (gradient
  < 15 j … > 120 j), les signaux croisés, le décideur nommé, le canal clair et
  le besoin identifié. Renvoie le score **et son explication**.
  Paliers : **8–10 chaud**, **5–7 moyen**, **0–4 froid**.
- **Canal recommandé** — `services/channel_recommendation.py`. Téléphone pour
  reprise/changement de proprio, email pour les hôtels/structures, Instagram
  pour un indépendant en ouverture avec signal social, LinkedIn si décideur
  identifié, téléphone par défaut. Renvoie le canal **et son explication**.
- **Messages** — `services/message_generation.py`. 4 variantes (DM Instagram,
  email, LinkedIn, script d'appel) personnalisées avec établissement, ville,
  signal, timing, besoin et l'offre du fournisseur (réglages Settings).

---

## Ingestion de leads réels — ETL BODACC

L'app sait récupérer de **vrais leads CHR** depuis l'API publique BODACC
(annonces commerciales, opendatasoft, sans authentification) et les injecter dans
la base via le même scoring / canal que les données seedées.

C'est un **ETL léger** :
- **Extract** — `app/ingestion/bodacc.py` interroge l'API (filtre Île-de-France /
  départements, familles d'avis création / vente / modification, mots-clés CHR,
  fenêtre de date) et pagine.
- **Transform** — enrichissement Sirene (`app/ingestion/enrichment/`), puis
  classification CHR et `app/ingestion/pipeline.py` (mapping, besoins/timing/décideur,
  scoring, canal).
- **Load** — upsert SQLite avec déduplication sur `(source, source_ref)`.

### Enrichissement Sirene (qualité)

Chaque lead est enrichi par son **SIREN** via l'API publique
`recherche-entreprises.api.gouv.fr` (données Sirene/INSEE, **sans clé**) :
- **code NAF** → classification CHR fiable qui **écarte les faux positifs**
  (holdings immobilières, sièges sociaux dont l'objet social mentionne "restaurant")
  — le NAF fait autorité, le repli par mots-clés ne sert que sans NAF ;
- **enseigne / nom commercial** → comble les noms manquants (les entreprises
  individuelles n'ont qu'un nom civil dans BODACC) ;
- **adresse normalisée** du siège ;
- **état administratif** → les établissements fermés sont écartés.

> L'API INSEE Sirene "brute" (api.insee.fr) exige une clé ; on utilise ici le
> service public ouvert qui repose sur les mêmes données. Désactivable avec
> `--no-enrich` (CLI) ou `{"enrich": false}` (endpoint).

Chaque lead importé porte `source="bodacc"` (badge **BODACC** dans l'UI), une vraie
URL d'annonce comme preuve, et coexiste avec les données démo (`source="demo"`).

### Stratégie de mise à jour (curseur + réconciliation)

L'ingestion ne re-balaie pas tout chaque jour. Quatre modes complémentaires :

| Mode | Rôle | BODACC | Sirene |
|------|------|--------|--------|
| `window` | fenêtre fixe (`--since` jours) | ✅ | ✅ |
| `incremental` | **passe A** : nouveaux leads depuis le curseur (`max(detection_date)` − chevauchement) | ✅ | ✅ |
| `reenrich` | **passe B** : guérit les leads `naf IS NULL` via le SIREN déjà stocké, **et supprime les faux positifs** confirmés non-CHR | ❌ | ✅ |
| `backfill` | **filet de sécurité** : large fenêtre, comble les annonces jamais récupérées | ✅ | ✅ |

Principe : *le curseur est une optimisation, la réconciliation garantit la justesse.*
- **Détection de troncature** : chaque run compare `fetched` à `total_available`
  (le `total_count` BODACC). Si la fenêtre est incomplète → `truncated=True` +
  alerte (jamais de cap silencieux) → lancer un `backfill`.
- `reenrich` est **indépendant des dates** (il cible un état, pas une période) :
  un trou d'enrichissement de juin est rattrapé en juillet sans souci.
- Cadence type : `incremental` + `reenrich` chaque jour, `backfill` chaque semaine.

### Lancer une ingestion

```bash
# CLI — modes
cd backend
python -m app.ingestion.run --mode window --since 60 --limit 200
python -m app.ingestion.run --mode incremental          # passe A (nouveaux)
python -m app.ingestion.run --mode reenrich             # passe B (guérison, Sirene-only)
python -m app.ingestion.run --mode backfill --since 120 # filet de sécurité
python -m app.ingestion.run --mode window --departments 75,92,93,94 --since 90 --reset

# ou via l'API
curl -X POST http://localhost:8000/api/dev/ingest \
  -H "Content-Type: application/json" \
  -d '{"source":"bodacc","since_days":60,"limit":100}'
curl -X POST "http://localhost:8000/api/dev/ingest/incremental"
curl -X POST "http://localhost:8000/api/dev/reenrich"
curl -X POST "http://localhost:8000/api/dev/ingest/backfill?since_days=120"

# ou via l'UI : bouton « Importer (BODACC) » sur la page Opportunités
```

Réponse type : `{fetched, chr_matched, created, updated, skipped_dupes, errors}`.
Relancer la même ingestion ne crée pas de doublons (`created=0, updated=N`).

### Tests

```bash
cd backend
python -m pytest tests/ -q   # classifier + mapping BODACC (sans réseau)
```

## Limites du PoC

- Données 100 % **seedées** ; aucune source externe réellement branchée.
- Scoring & canal **calculés au seed** et stockés (recalcul = relancer le seed).
- Scores volontairement « optimistes » (liste curée de moments d'achat) — la
  calibration fine est un sujet V2.
- Pas d'auth, pas de multi-utilisateur, pas de persistance des messages OpenAI
  au-delà du dernier appel, pas de drag & drop natif sur le kanban (déplacement
  par menu déroulant).
- Responsive pensé **desktop** (mobile non optimisé).

## Idées de V2

- Connecteurs réels : BODACC/INPI (annonces légales, créations), France Travail
  (recrutement), Google Maps/Places (avis, photos travaux), réseaux sociaux.
- Pipeline d'enrichissement automatique + déduplication + scoring temps réel.
- Drag & drop kanban, rappels de relance, séquences multi-touch automatisées.
- Envoi réel (email/SMS/LinkedIn) + tracking d'ouverture/réponse.
- Multi-fournisseurs / multi-secteurs, auth, rôles, export CRM.
- A/B testing des messages, mesure du taux de réponse par canal/signal.
```
