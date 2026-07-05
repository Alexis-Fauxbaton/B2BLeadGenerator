# Architecture — CHR Signal Radar

Dernière mise à jour : 2026-07-06 (post-merge brique 1 « siret_matcher »).
Ce document décrit le système en deux temps : une **vue d'ensemble** (ce que fait
le produit et comment les morceaux s'emboîtent), puis un **deep dive** par
sous-système. Les docs de design historiques (dans `docs/`) restent la référence
des décisions ; ici on décrit l'état construit.

---

## 1. Vue d'ensemble

### Le produit

SaaS B2B (PoC/MVP) qui **détecte, qualifie et suit des opportunités commerciales
dans le CHR** (cafés, hôtels, restaurants) pour des fournisseurs — fournisseur
de démo : LumaPro (luminaires/mobilier). Le moment de vente clé est la fenêtre
d'aménagement : 1 à 4 mois avant l'ouverture d'un établissement. Le système
cherche donc des **signaux d'achat** (ouverture prochaine, création récente,
reprise, changement de propriétaire) et les transforme en leads scorés,
enrichis en contacts, avec canal d'approche recommandé et messages générés.

### Stack

| Couche | Techno | Dossier |
|---|---|---|
| Frontend | Next.js 14 (App Router) + TypeScript + Tailwind | `frontend/` |
| Backend API | FastAPI + SQLModel | `backend/app/` |
| Base | SQLite (fichier), migrations légères maison | `backend/chr_signal_radar.db` |
| ETL | Python pur, connecteurs + enrichisseurs fail-soft | `backend/app/ingestion/` |
| IA | OpenAI (`gpt-4o-mini` par défaut) avec repli local systématique | juges, arbitre, messages |

### Le système en un schéma

```
      SOURCES (Extract)                 TRANSFORM                      LOAD / SERVE
┌──────────────────────────┐   ┌─────────────────────────┐   ┌─────────────────────────────┐
│ BODACC (annonces légales)│   │ SireneEnricher          │   │ Upsert SQLite               │
│  créations/ventes/modifs │──►│  NAF, enseigne, adresse,│──►│  dédup (source, source_ref) │
│                          │   │  état, lat/lon          │   │  + Signal + ContactHistory  │
│ Instagram via Apify      │   │ naf_classifier /        │   └──────────────┬──────────────┘
│  hashtags → posts bruts  │──►│  chr_classifier (type)  │                  │
│  → profils               │   │ siret_matcher (SIREN)   │   ┌──────────────▼──────────────┐
└──────────────────────────┘   │ ContactEnricher         │   │ FastAPI /api/*              │
                               │  Places → OSM → site web│   │  dashboard, opportunities,  │
        PASSES RÉCURRENTES     │ scoring + canal + segment│  │  pipeline (kanban), messages│
  incremental / backfill /     └─────────────────────────┘   │  settings, eval, dev        │
  reenrich / contact / refresh                               └──────────────┬──────────────┘
  (guérison, fermetures,                                                    │
   heartbeat de fraîcheur)                                    Next.js (fetch JSON, no auth)
```

### Les deux sources aujourd'hui — et la cible

- **BODACC** (registre, gratuit) : exhaustif sur les événements *légaux*
  (création, reprise, changement de propriétaire) mais aveugle sur le *stade*
  (travaux ? déjà ouvert ?) et pauvre en contact direct.
- **Instagram** (Apify, payant au résultat) : voit le stade réel (travaux,
  teasing, « SOON ») et donne le canal chaud (DM), mais échantillonne mal
  (hashtags) et exige un tri qualité.
- **Cible (pivot « inventaire », cf. §9)** : le registre fait le volume/recall,
  Instagram fait la précision/timing/contact, et le **matching SIRET**
  (construit, brique 1) relie les deux pour corroborer.

---

## 2. Modèle de données (`backend/app/models.py`)

Quatre tables SQLModel :

- **`Opportunity`** — l'entité centrale (un lead = un établissement).
  - *Identité/provenance* : `establishment_name`, `establishment_type`, `city`,
    `address`, `source` (`demo`/`bodacc`/`instagram`), `source_ref` (clé de
    dédup par source : n° d'annonce BODACC, handle Instagram), `siren`, `naf`.
  - *Signal/scoring* : `main_signal`, `secondary_signals` (JSON),
    `detection_date`, `activity_start_date`, `venue_origin_date` (date du
    *local* via l'exploitant précédent — distingue vraie ouverture et reprise
    d'un vieux fonds), `estimated_timing`, `probable_needs` (JSON),
    `decision_maker`, `dirigeants` (JSON), `opportunity_score`, `score_reason`,
    `recommended_channel`, `channel_reason`, `proof_text`, `proof_url`.
  - *Contact* : `phone`, `email`, `website`, `instagram`, `facebook`,
    `extra_addresses`/`extra_emails` (JSON), `latitude`/`longitude`,
    `review_count` (proxy de fraîcheur Places), `contact_confidence`,
    `decision_maker_email`, `decision_maker_confidence`.
  - *Cycle de vie* : `status` (kanban), `contact_enriched_at` (tentative de
    passe contact), `last_checked_at` (heartbeat refresh), `closed_at`,
    `next_follow_up_date`, messages générés (4 champs).
- **`Signal`** — piste d'audit : chaque signal détecté (type, source, URL,
  date, confiance, texte brut), FK vers l'opportunité, cascade delete.
- **`ContactHistory`** — journal d'actions (message généré, changement de
  statut, relance, note), ordonné desc.
- **`Settings`** — ligne unique : identité du fournisseur (nom, offre, ton,
  zone) qui paramètre la génération de messages.

**Migrations légères** (`database.py`) : pas d'Alembic. `init_db()` fait
`create_all` puis `_run_lightweight_migrations()` : inspection des colonnes de
`opportunities`, diff contre un dict `colonne → ALTER TABLE ... ADD COLUMN`,
exécution des manquantes. Ajouter un champ = 1 ligne dans le modèle + 1 entrée
dans ce dict.

---

## 3. Le flux d'ingestion (`backend/app/ingestion/`)

### Interface commune (`base.py`)

`Connector` (ABC) : `fetch(...) -> List[dict]` (brut) et
`to_candidates(records) -> List[LeadCandidate]`. `LeadCandidate` est le
dataclass pivot que TOUT le pipeline consomme (identité, signal, contact,
SIREN/NAF, flags `enriched`/`closed`, `raw`).

### `_process_candidate` (pipeline.py) — le tronc commun

Chaque candidat, quelle que soit sa source, passe par :

1. **Enrichissement Sirene** (si activé) : NAF, enseigne, adresse, état ; un
   établissement fermé est écarté ; une reprise déclenche un 2e lookup sur le
   SIREN précédent pour dater le local (`venue_origin_date`).
2. **Classification CHR** : le **NAF fait autorité** quand il existe
   (`classify_naf` — évite les holdings dont l'objet social mentionne
   « hôtel, restaurant ») ; sinon type déjà validé (Instagram) ; sinon repli
   mots-clés (`chr_classifier.classify`). Non-CHR → écarté.
3. **Dédup** intra-batch puis persistante sur `(source, source_ref)`.
4. **Scoring + canal** (services, cf. §6) ; besoins probables par type
   (`NEEDS_BY_TYPE`), timing par signal (`TIMING_BY_SIGNAL`).
5. **Upsert** : création (avec `Signal` + `ContactHistory` « ingested ») ou
   mise à jour en place (les signaux/décideur sont rafraîchis pour que les
   améliorations de parsing corrigent l'existant).

### Connecteur BODACC (`bodacc.py`)

API opendatasoft `annonces-commerciales`, filtrée IdF (ou départements
explicites), familles création/vente/modification, mots-clés CHR, fenêtre
temporelle ; pagination avec retry. Décisions clés de parsing :

- famille → signal (`création récente` / `reprise` / `changement propriétaire`),
- une « création » est **requalifiée en reprise** si `origineFonds` mentionne
  un achat/précédent exploitant (le champ registre prime sur la famille),
- une « modification » sans changement d'exploitant est écartée (pas un moment
  d'achat),
- extraction des `dirigeants` depuis le texte libre `administration`
  (hiérarchie Président > Gérant > DG > ...), qui alimente le bonus
  « décideur nommé » du score.

### Source Instagram (`instagram.py` + `run_instagram`)

Funnel actuel (sera recâblé en brique 3, cf. §9) :

1. `scrape_hashtags` — actor Apify hashtag (posts bruts, tous secteurs).
2. `discover` — heuristique **pure** CHR + Île-de-France sur le texte du post,
   dédup par handle → `{handle, name, city, type, caption}`.
3. `judge` — juge LLM batch sur caption : fraîcheur (`opening`/`just_opened`
   gardés) + `is_venue_owner` (écarte les comptes média qui parlent d'un lieu
   à la 3e personne — le handle ne serait pas le lieu).
4. `profile_enrich` — scrape des profils (actor profil Apify) sur les
   survivants ; garde-fous déterministes (`postsCount > 150` → établi ;
   `_profile_long_history` → plusieurs posts vieux de +150 j) ; puis juge LLM
   **unitaire** par profil (établi → drop) qui extrait aussi adresses/emails ;
   enrichit ville/site/`bio_snippet`.
5. `run_instagram` — **matching SIREN** via `siret_matcher.match()` (cf. §4),
   signal déduit de la fraîcheur, puis tronc commun `_process_candidate`.

### Les passes récurrentes (pipeline.py, CLI `app.ingestion.run`)

| Mode | Rôle |
|---|---|
| `incremental` | nouveaux leads depuis le curseur (max `detection_date`) + chevauchement de sécurité |
| `backfill` | re-balaye une large fenêtre (filet anti-trous : rate-limit, crash) |
| `reenrich` | guérit les leads `naf IS NULL` via le SIREN stocké ; supprime les faux positifs confirmés (NAF non-CHR) |
| `contact` | enrichissement contact des leads jamais tentés (`contact_enriched_at IS NULL`) |
| `refresh` | re-vérifie les actifs : fermetures (Sirene état ≠ A → « perdu » + Signal), heartbeat `last_checked_at` |

---

## 4. Matching SIREN/SIRET (`enrichment/siret_matcher.py`) — brique 1 du pivot

**Problème** : relier un lead Instagram (`@moka.paris`) à son entreprise au
registre alors que le nom légal n'a souvent aucun rapport (« SASU BJ » exploite
« CALA ROYA »). Remplace l'ancien `backfill_siren` (supprimé).

**Chaîne à 3 étages** — chaque étage ne traite que ce que le précédent n'a pas
résolu ; API publique unique `match(name, city, postal, address, context) ->
Optional[MatchResult{siren, siret, naf, enseigne, confidence, method}]` :

1. **Nom** : `clean_name` (NFKC — lettres stylisées 𝐺𝑖𝑜𝑟𝑔𝑖𝑛𝑎 → Giorgina ;
   strip emojis + variation selectors ; premier segment avant `|•–`/tiret)
   → `recherche-entreprises /search` → `pick_by_name` : NAF CHR + token
   distinctif commun + **cohérence géo obligatoire** (CP ou ville — tokenisée
   par `_city_tokens`, SANS le filtre de mots génériques : « paris » est
   générique dans un nom d'enseigne, pas comme ville). Match → confiance
   `haute`, méthode `nom`.
2. **Adresse** : adresse du lead → géocodage BAN (score ≥ 0.6 sinon refus —
   un géocodage flou pointe la mauvaise rue) → `/near_point` (rayon 0,1 km,
   section NAF I) → `pick_by_address` : candidats CHR au **même numéro de
   voie** ; un seul → match `moyenne`/`adresse` ; plusieurs (succession
   d'exploitants) → pool d'arbitrage.
3. **Arbitre LLM** (`arbitrate`, unitaire, fail-soft) : candidats ambigus +
   contexte (bio + captions, 600 c). Trois leçons durement acquises, encodées :
   - **jamais de merge nom-seul sans géo ni arbitre** (piège Auréa : un
     « AUREA » CHR existe, la bio « bijoux, Portugal » doit le rejeter) ;
   - **l'arithmétique de dates se fait en code, pas dans le prompt** :
     `_age_label` précalcule « créé il y a 12 mois / activité démarrée il y a
     1 mois » (gpt-4o-mini échoue sur les dates brutes — il raisonne depuis
     son époque d'entraînement) + ancre « date du jour » dans le prompt ;
   - champ `reasoning` exigé avant `match_index` (fiabilise le suivi de règles).

**Garanties** : fail-soft partout (échec réseau/LLM → lead sans SIREN, jamais
d'ingestion cassée) ; throttle 0,15 s (limite 7 req/s de l'API) ; transport
HTTP injectable (`fetch`) et client LLM injectable (sentinel `_USE_ENV` ;
`None` = explicitement sans arbitre → tests 100 % déterministes).

**Mesuré** (éval, cf. §7) : 8/9 matchs attendus, **0 faux merge** (gate dur).
Un non-match n'est pas un échec : la réconciliation (brique 4) retentera — le
temps joue pour nous (l'adresse arrive en bio, le SIRET est créé, l'enseigne
est renseignée à l'approche de l'ouverture).

---

## 5. Enrichissement contact (`enrichment/`)

Cascade **fail-soft, qui ne remplit que les champs vides** :

1. **`sirene.py`** (`SireneEnricher`) — recherche-entreprises par SIREN ;
   cache mémoire + throttle ; NAF/enseigne/adresse/état/lat-lon.
2. **`places.py`** — Google Places « New » searchText (clé optionnelle
   `GOOGLE_PLACES_API_KEY`). Validation stricte anti-faux-match : type CHR
   obligatoire + localisation confirmée par distance ≤ 200 m du point Sirene
   (`match_basis="geo"`, fort) ou par texte CP/ville (faible). La distance ne
   met jamais de veto (un siège peut être loin). Donne téléphone/site/nb
   d'avis.
3. **`osm.py`** — Overpass (gratuit) : POI CHR à 150 m avec recoupement de nom
   → téléphone/site/instagram/email/facebook.
4. **`website_scraper.py`** — homepage + pages contact/mentions légales (max
   3 pages, 500 Ko) : email (`mailto:` préféré), réseaux, téléphone.

**Routage qualité** (`contact_quality.py`) : un email nominatif ou corroboré
par le nom du dirigeant va sur `decision_maker_email`, un role-based sur
l'établissement ; `contact_confidence="haute"` UNIQUEMENT si le match Places
est géo-confirmé ; le `review_count` n'est stocké/scoré que si le match est
fiable (sinon il vient probablement d'un autre établissement).

---

## 6. Services métier (`backend/app/services/`)

- **`scoring.py`** — score 0-10 additif et **explicable** (`score_reason`) :
  gradient de fraîcheur du signal (+2 → -2 selon l'âge vs 15/30/90/120 j),
  bonus par type de signal (ouverture/reprise +3, rénovation/recrutement +2),
  bonus multi-signaux par *familles* distinctes, bonus qualification (décideur
  nommé, canal non-défaut, ≥ 2 besoins), pénalité segment `service` (-2,
  ex. traiteur 56.21Z — pas de salle à aménager), raffinement `review_count`
  (≤ 20 avis = fenêtre fraîche +1 ; ≥ 200 = établi -1).
- **`channel_recommendation.py`** — cascade de règles : reprise → téléphone ;
  hôtel → email ; ouverture + présence sociale → instagram ; décideur nommé →
  linkedin ; défaut téléphone. Chaque branche explique (`channel_reason`).
- **`segment.py`** — `venue` (salle à aménager) vs `service` (traiteur…).
- **`lifecycle.py`** — états **dérivés, jamais stockés** : stade
  (pré-ouverture / ouvert récemment / établi / fermé), chaleur (chaud/tiède/
  froid selon l'âge du signal d'achat), fraîcheur de la donnée (vs
  `last_checked_at`).
- **`message_generation.py`** — 4 messages (DM Insta, email, LinkedIn, script
  d'appel) personnalisés par le contexte lead + `Settings` fournisseur ;
  OpenAI si clé, sinon **templates locaux** (le produit marche sans clé).

---

## 7. Évaluation (`backend/app/ingestion/eval/`)

Harnais d'éval **sur snapshots figés** (20 profils Instagram réels dans
`snapshots/`, vérité terrain annotée dans `instagram_groundtruth.csv`) —
reproductible, sans re-scrape. Deux évals :

1. **Classification** (`run.py`, page `/eval` du front) : projette le verdict
   du pipeline (gardé/écarté) sur les labels vérité (`opening`, `just_opened`,
   `established`, `chain_multisite`, `not_venue`, `noise`). Métriques :
   précision du bucket « à contacter », rappel des openings.
2. **Matching** (`match_eval.py`) : colonne `expected_siren` (9 SIREN validés
   à la main, corrections documentées dans le rationale). HTTP Sirene/BAN figé
   en **fixtures record/replay** (`fixtures/match/`) ; l'arbitre LLM reste
   live. **Gate dur : 0 faux merge** (`false_merge`/`wrong_siren` → exit 1) ;
   les fixtures manquantes sont signalées bruyamment (sinon le gate pourrit en
   silence). `--record` ré-enregistre live, défaut = replay offline.

Convention : tout changement du matcher ou du funnel passe par ces évals
avant merge ; les cas célèbres (MOKA, Tre Gusto/OCOIN, Auréa, Chick'n Tikka)
sont les tests de régression nommés.

---

## 8. API & Frontend

**Backend** (`main.py`, CORS ouvert, pas d'auth — PoC) :

| Route | Rôle |
|---|---|
| `GET /api/dashboard/stats` | agrégats (totaux, leads chauds ≥ 8, relances dues, répartitions) |
| `GET/PATCH /api/opportunities[/{id}]` | liste filtrable/triable, détail, mise à jour (+ note journalisée) |
| `PATCH /api/opportunities/{id}/status` | changement de statut kanban + relance planifiée |
| `POST /api/opportunities/{id}/generate-messages` | génération des 4 messages |
| `GET /api/pipeline` | colonnes kanban par statut |
| `GET/PATCH /api/settings` | identité fournisseur (singleton) |
| `GET /api/eval/instagram` | résultat d'éval (cache fichier) |
| `POST /api/dev/*` | déclencheurs dev : seed, ingest, reenrich, contact-enrich, refresh, instagram |

**Frontend** (Next.js 14, tout `"use client"`, fetch direct sans lib d'état) :
Dashboard, Opportunités (liste + détail 4 onglets), Pipeline (kanban), Éval
Instagram, Settings. `lib/api.ts` centralise les appels
(`NEXT_PUBLIC_API_URL`, défaut `localhost:8000`), `lib/labels.ts` les libellés
français, `components/Badges.tsx` les badges score/signal/statut/canal.

---

## 9. Vision cible : le pivot « inventaire + étiquetage »

Décision produit du 2026-07-05 (spec : `docs/inventory-pivot-design.md`).
Constat : le funnel Insta décidait la fraîcheur sur des captions (preuve
pauvre) ; Instagram ne peut pas faire le volume ; le registre voit toutes les
ouvertures (immatriculation obligatoire, 1-6 mois avant). Cible : passer d'un
funnel qui **filtre** (drop irréversible) à un inventaire qui **étiquette**
(tous les CHR en base, label de cycle de vie réévaluable, `opening_soon` =
segment de tête, qualité par corroboration registre × Instagram).

| Brique | Contenu | État |
|---|---|---|
| 1. `siret_matcher` | matching Insta↔SIRET (nom → adresse → arbitre) + éval fixtures | **Fait** (mergé 2026-07-06) |
| 2. Délta-Sirene | nouveaux SIRET NAF 55/56 par jour = recall ~100 % sur les ouvertures ; corroboration croisée | À faire (prochaine) |
| 3. Funnel v2 + cache verdicts | juge unique par compte sur dossier complet, labels de cycle de vie, `handle_verdicts` avec fenêtres de revisite (not_venue 12 mois, established 6, noise 2, opening = watchlist) | À faire |
| 4. Watchlist + réconciliation | re-scrape hebdo des opening-soon, re-matching des leads sans SIREN | À faire |

Dettes/leçons consignées pour les briques suivantes (ledger de la brique 1) :
persister `siret` + `method`/`confidence` du matching sur l'Opportunity (la
brique 2 corrobore par SIRET), règle déterministe « succession au même
numéro → le plus récent », corriger `_judge_profile` (même bug d'ancrage de
date que l'arbitre), télémétrie des matchs + audit hebdo pour faire grandir la
vérité terrain sur des échecs réels.

**Contrainte API découverte (2026-07-06)** : recherche-entreprises n'a **pas
de filtre par date de création** (paramètre inconnu ignoré en silence) et
plafonne à 10 000 résultats paginés → impossible d'y faire le délta. La brique
2 passera par l'**API Sirene INSEE** (`api.insee.fr/api-sirene/3.11/siret`,
requête `dateCreationEtablissement:...`), clé gratuite requise (portail
portail-api.insee.fr), fail-soft sans clé.

---

## 10. Conventions transverses & pièges

- **Fail-soft partout** : aucun enrichisseur/juge/matcher ne doit jamais
  casser l'ingestion. Pas de clé API → repli dégradé documenté, pas de crash.
- **Python 3.9** (venv backend) : `Optional[X]`, jamais `X | None`.
- **Le NAF fait autorité** sur les mots-clés pour la classification CHR.
- **Précision d'abord** : un contact/`review_count`/SIREN douteux est ignoré
  plutôt que stocké faux (il polluerait scoring, dédup et fusion en aval).
- **Explicabilité** : chaque score, canal et verdict porte sa raison en champ
  texte — l'UI l'affiche, l'utilisateur doit pouvoir contester.
- **Provenance** : `source` + `source_ref` + `siren` sur chaque lead ;
  les passes de guérison (`reenrich`) s'appuient dessus.
- **Docstrings et libellés en français** ; console Windows = cp1252 (pas
  d'emoji dans les `print`, `PYTHONIOENCODING=utf-8` au besoin).
- **Ne pas lancer `npm run build` pendant `npm run dev`** (conflit `.next`) ;
  uvicorn avec `--reload`.
- **Tests** : `cd backend && python -m pytest tests/ -q` ; évals :
  `python -m app.ingestion.eval.run` (classification, LLM live) et
  `python -m app.ingestion.eval.match_eval` (matching, offline).
