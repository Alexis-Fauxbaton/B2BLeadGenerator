# B — Volume max (stock Sirene + balayage Google Places) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL — `superpowers:subagent-driven-development` (recommandé) ou `superpowers:executing-plans` pour exécuter tâche-par-tâche. Étapes en `- [ ]` pour le suivi. Chaque tâche porte un **Modèle d'exécution recommandé** (sonnet mécanique / opus câblage+éval).

**Goal :** faire passer la population `architecte` de **~1 000 leads** (A1 Insta 44 + A2 annuaires ~850 + délta ~150) à **PLUSIEURS MILLIERS de leads qualifiables** (~30 000, dont ~28 000 issus du stock) via DEUX connecteurs de découverte de masse, EN PLUS d'A1/A2 et SANS jamais toucher au CHR ni à ses évals :

1. **B1 — STOCK Sirene** (`source='sirene_stock'`) : l'intégralité du stock d'établissements ACTIFS NAF **74.10Z** (design, **308 629 unités INSEE** actives, mesuré live) + **71.11Z** (architecture) — total des deux NAF état=A = **449 569** unités INSEE, pas une fenêtre de création. Découpage par **département** (curseur INSEE, aucun plafond de pagination — pagination pilotée par le curseur jusqu'à épuisement `curseurSuivant == curseur`, cf. sonde), filtre de **qualification mots-clés au rendement mesuré** (réutilise `jeunes_studios.qualifies`, 9,3 % du stock 74.10Z → ~28 000 qualifiés) + **gardes négatives faux-amis** (design graphique/produit/corporate fit-out), `[ND]` écartés (VIDE > FAUX), `lifecycle_label='unknown'` (sauf booster « création récente <18 mois »), **SIREN/dirigeant/ancienneté NATIFS** (données INSEE, aucun matcher requis).
2. **B2 — Balayage Google Places** (`source='places'`) : Text Search (New) par **ville ordonnée par population**, champs Contact UNIQUEMENT (`nationalPhoneNumber`/`websiteUri`/`userRatingCount`, `maxResultCount=20` → 20 fiches enrichies par appel facturé — SKU **Text Search Enterprise**), `phone`/`website` natifs → `contact_confidence` selon la sémantique existante, **budget dur paramétrable en € par run**, **curseur de reprise** (checkpoint mensuel) pour étaler sur plusieurs jours. Fortement ADDITIF (recouvrement mesuré : 7 collisions de tokens génériques, toutes écartées → **0/59** sous token-set exact + corroboration).

**Dédup inter-sources** : on GÉNÉRALISE le soft-merge nom+ville d'A2 (nom+ville normalisés + **corroboration obligatoire**). **Corroboration FORTE requise** (téléphone OU domaine site identique) pour un merge entre DEUX sources de masse (`sirene_stock`↔`places`, aucune n'étant annuaire/insta) ; CP-seul/dirigeant restent tolérés uniquement quand un côté est annuaire/insta (voir Décision #11). Les recouvrements **ENRICHISSENT la fiche survivante** (le téléphone Places comble un lead Insta muet) au lieu de créer des doublons. Gate : **0 faux merge sur fixtures réelles** (fixture adverse homonyme même nom + même CP incluse).

**Priorisation** : le volume ne noie PAS les leads chauds. Score/tri = **hospitality evidence (+2)** > **création récente (<18 mois, booster moment, +1)** > **contact complet (téléphone, départage)** > reste (`prescripteur actif` + `stock sirene`/`annuaire places` en famille NEUTRE → score bas → fond de liste). **Attention** : `stock sirene` et `annuaire places` doivent être ajoutés à une famille NEUTRE de `SIGNAL_FAMILY` (Task 5) — sinon, libellés inconnus, ils comptent chacun pour une famille distincte et déclenchent à tort le bonus « signaux croisés » (+1), et un lead fraîchement ingéré (detection_date=aujourd'hui) prend déjà +2 « signal très récent » : un simple lead stock scorerait ~3, pas 0. Le tri ne doit donc PAS reposer sur le score brut seul pour séparer volume/chaud → on s'appuie aussi sur tier/labels (voir Task 5). L'UI liste devient **paginée côté backend** (l'endpoint actuel renvoie `.all()` sans limite — inutilisable à 5 000+ lignes).

**Fondé sur** la sonde `.superpowers/sdd/sonde-volume/` (bruts : `dept_breakdown.json`, `places_summary.json`, `details_summary.json`, `eyeball_30.json`, `dedup_ufdi_probe.json`, `existing_architecte_leads.json`). Décisions tranchées ci-dessous.

**Architecture (delta vs A1/A2, tout ADDITIF)** :
- **`insee.py`** : ajout de `build_stock_query` + `fetch_stock_etablissements` (requête stock SANS fenêtre de date, `periode(NAF AND état=A)` + CP, curseur, `last_cursor` exposé pour reprise). Les fonctions delta existantes (`build_query`, `fetch_new_etablissements`) ne sont **PAS modifiées** (tests `sirene_delta`/`jeunes_studios` bit-à-bit intacts).
- **`SireneStockConnector`** (`source='sirene_stock'`, `population='architecte'`) : sibling de `JeunesStudiosConnector`, réutilise `qualifies`/`_best_name`/`_address`/`_ymd`, mapping natif SIREN/dirigeant/ancienneté, label recency `<18 mois`.
- **`places.py`** : ajout de `search_places_text` (pagination `pageToken`, field mask Contact, `maxResultCount=20`) SANS toucher `lookup_places`/`_match_ok`/le gate CHR (`CHR_PLACE_TYPES` intact → `contact_enricher` CHR inchangé).
- **`PlacesArchiConnector`** (`source='places'`, `population='architecte'`) + `data/villes_fr.py` (top communes par population) + checkpoint budget/reprise.
- **`pipeline.py`** : dédup généralisée (`SOFT_DEDUP_SOURCES = {annuaire, sirene_stock, places}`), `_corroborates` += téléphone, phone-depuis-`raw` généralisé, `CONNECTORS`/`SOURCE_LABELS` += stock/places, `run_places`, CLI/endpoints.
- **`scoring.py`** : famille `PRESCRIBER_FRESH` (+1, booster moment) — additive, jamais émise par un lead CHR → scores CHR bit-à-bit inchangés. **+ mapper `stock sirene`/`annuaire places` sur une famille NEUTRE dans `SIGNAL_FAMILY`** (sinon bonus « signaux croisés » parasite, cf. Priorisation).
- **`routes/opportunities.py`** : pagination `limit`/`offset` + `X-Total-Count` + tri composite `score desc, téléphone présent desc, detection_date desc`. Frontend : pager.

**Tech Stack :** Python 3.9 (`Optional[X]`/`Dict`/`List`/`Tuple` de `typing`, **jamais** `X | None`), console cp1252 → `PYTHONIOENCODING=utf-8`, docstrings/commentaires/prompts **en français**, `requests` (déjà présent), SQLModel/SQLite. **Aucune nouvelle dépendance** (bs4 déjà installé par A2). Réutilise `Connector`/`LeadCandidate` (`base.py`), `insee` (curseur), `jeunes_studios.qualifies`/`sirene_delta` helpers, `_process_candidate`/`_merge_corroboration`/`_soft_dedup_architecte`/`_corroborates` (`pipeline.py`), `siret_matcher._tokens`/`_city_tokens`/`_domain`.

---

## Global Constraints

- **Python 3.9** ; **fail-soft partout** : pas de clé INSEE → stock `[]` ; pas de clé Google → Places `[]` ; réseau KO → liste partielle, jamais d'exception ; budget € atteint → arrêt propre du balayage (pas d'erreur).
- **Aucun appel réseau dans les tests unitaires** : tout HTTP est injecté (`fetch`/`http_fetch`) et alimenté par des **fixtures = extraits des bruts de la sonde** (`.superpowers/sdd/sonde-volume/`). Réseau autorisé UNIQUEMENT au **run réel borné** (T6, manuel, hors pytest).
- **Throttle POLI** : INSEE 2,1 s (intégré `insee.py`) ; Places — pas de throttle imposé par Google mais on garde 0,2 s entre appels + **budget € dur** comme régulateur. `User-Agent` honnête déjà posé (`chr-signal-radar`).
- **Volumes/coûts CHIFFRÉS partout** (cf. « Décisions »). Budget Places par défaut **10 €/run**.
- **Répertoires** : `python`/`pytest` depuis `chr-signal-radar/backend` avec `.venv\Scripts\python.exe` ; `git` depuis la racine `chr-signal-radar/`. Branche **`feature/b-volume-max`**. **Pas de push, pas de `--no-verify`.** CLI exécuté **SYNCHRONE**.
- `python -m pytest tests/ -q` **vert à la fin de CHAQUE tâche**.
- **ÉVALS EXISTANTES INTACTES (non négociable)** :
  - CHR : `app.ingestion.eval.run` (gates `recall_opening == 1.0`, `hot_precision >= 0.60`) et `app.ingestion.eval.match_eval` (**8/9, 0 faux merge**) NE BOUGENT PAS. `match()` CHR, `classify_naf`, `CHR_PLACE_TYPES` **jamais** modifiés.
  - A1/A2 : `app.ingestion.eval.prescripteurs_run` (gates `studio_actif_precision >= 0.70`, `0 hors_cible en tiers`, `0 faux merge annuaire×insta`) reste vert. `run_prescripteurs`/`run_annuaires` (leur comportement A1/A2) **jamais** dégradés ; la fusion douce reste **asymétrique** (déclenchée par la source ENTRANTE, jamais par un lead Insta entrant → A1 bit-à-bit identique).
- **TDD strict** : tests d'abord (RED), puis implémentation (GREEN), puis commit avec le message exact fourni.
- **Créer la branche avant la Task 1** (depuis la racine) :

```bash
git checkout -b feature/b-volume-max
```

---

## RUNBOOK « lancement demain » (à exécuter APRÈS le merge de ce plan)

> Séquence exacte pour demain matin. Toutes commandes SYNCHRONES depuis `chr-signal-radar/backend` avec `PYTHONIOENCODING=utf-8` et `.venv\Scripts\python.exe`. Budgets et durées estimés d'après la sonde. **Chaque étape a un point de contrôle chiffré (SQLite) et on ne passe à la suivante que si le gate de non-régression est vert.**

### 0. Pré-vol (gates de non-régression AVANT toute ingestion)

```bash
cd chr-signal-radar/backend
$env:PYTHONIOENCODING="utf-8"
.venv\Scripts\python.exe -m pytest tests/ -q                              # tout vert
.venv\Scripts\python.exe -m app.ingestion.eval.match_eval                 # 8/9, 0 faux merge
.venv\Scripts\python.exe -m app.ingestion.eval.prescripteurs_run          # gates A1+A2 OK
# (si OPENAI_API_KEY) .venv\Scripts\python.exe -m app.ingestion.eval.run   # gates CHR verts
```
**Gate :** les 3 évals OK. Sinon STOP (régression introduite par B → corriger avant de lancer).

Compter la base de départ :
```bash
.venv\Scripts\python.exe -c "from app.database import engine; from sqlmodel import Session, text; print(list(Session(engine).exec(text('SELECT source,population,count(*) FROM opportunities GROUP BY source,population'))))"
```

### 1. Annuaires A2 d'abord (stock pré-qualifié, le plus propre) — ~2 min

```bash
.venv\Scripts\python.exe -m app.ingestion.run --mode annuaires --annuaire cfai --limit 800
.venv\Scripts\python.exe -m app.ingestion.run --mode annuaires --annuaire ufdi --limit 200
```
**Point de contrôle :** `SELECT count(*) FROM opportunities WHERE source='annuaire'` → **~700–850** (CFAI ~700 hors honoraires + UFDI ~157). `soft_merges` visibles dans le retour CLI.

### 2. Délta jeunes studios A2 (flux récent, faible volume) — ~1 min

```bash
.venv\Scripts\python.exe -m app.ingestion.run --mode window --source jeunes_studios --since 30 --limit 1000 --departments france
```
**Point de contrôle :** `SELECT count(*) FROM opportunities WHERE source='jeunes_studios'` → **~120–160** (~5 qualifiables/jour × 30 j). `main_signal='prescripteur actif'`, `lifecycle_label='unknown'`.

### 3. STOCK Sirene B1 par département (le gros du volume) — ~16–30 min total

> **IMPORTANT — sémantique de `--limit`.** `--limit N` borne les enregistrements **BRUTS** INSEE récupérés, **PAS** les leads qualifiés (la qualification mots-clés — rétention ~9,3 % — a lieu APRÈS le fetch, dans `to_candidates`/`map_stock_etablissement`). Un `--limit 8000` ne rend donc ~744 qualifiés, pas 5 000, ET tronque À L'INTÉRIEUR du premier département (dept 69 ≈ 12k bruts, Paris ≈ 37k bruts, national deux NAF ≈ 450k) : les départements suivants ne sont jamais atteints. → **On ingère UN département à la fois** et on laisse le **curseur INSEE piloter la pagination jusqu'à épuisement** (`curseurSuivant == curseur`) : `--limit 0` = illimité (curseur-exhaustion), l'endpoint SIRET INSEE n'a AUCUN plafond de pagination. Le découpage par arrondissement n'est PAS une contrainte API : il n'est utile que si l'on choisit délibérément un `--limit` bas.

```bash
# Un département à la fois, curseur jusqu'à épuisement (--limit 0). Boucler sur tous les depts.
.venv\Scripts\python.exe -m app.ingestion.run --mode window --source sirene_stock --departments 69 --limit 0
# Point de contrôle après dept 69 : SELECT count(*) FROM opportunities WHERE source='sirene_stock'  -> ~1000-1200 qualifiés (12k bruts x 9,3 %)
.venv\Scripts\python.exe -m app.ingestion.run --mode window --source sirene_stock --departments 92 --limit 0
.venv\Scripts\python.exe -m app.ingestion.run --mode window --source sirene_stock --departments 13 --limit 0
# ... boucler sur l'ensemble des départements (ordonnés par densité 74.10Z, cf. dept_breakdown.json) ...
# Paris (75) — aucun scindage requis avec le curseur-exhaustion (37 291 bruts, curseur jusqu'au bout) :
.venv\Scripts\python.exe -m app.ingestion.run --mode window --source sirene_stock --departments 75 --limit 0
# (scindage par CP arrondissement UNIQUEMENT si l'on impose un --limit bas — pas une contrainte INSEE)
```
**Coût/durée :** ~449 569 établissements 74.10Z+71.11Z état=A INSEE / 1000 par requête = **~450 requêtes × 2,1 s ≈ 16 min** d'appels INSEE pour la France entière ; le reste = écritures SQLite (~28 000 candidats retenus après filtre 9,3 %). Gratuit (clé INSEE gratuite). *Requête de repro (HTTP 200) :* `periode((activitePrincipaleEtablissement:74.10Z OR activitePrincipaleEtablissement:71.11Z) AND etatAdministratifEtablissement:A)` → `header.total = 449569`.
**Point de contrôle final stock :** `SELECT count(*) FROM opportunities WHERE source='sirene_stock'` → **~25 000–30 000** (74.10Z 308 629 × 9,3 % ≈ 28 000). `SELECT count(*) FROM opportunities WHERE source='sirene_stock' AND naf='71.11Z'` → **quasi 0** (co-occurrence stricte archi+intérieur ≈ 0 en pratique, cf. décision #3 — c'est CORRECT, VIDE > FAUX).

### 4. Places B2 top-N villes — ~5 min, ≤ 10 €

> **Dépendance d'ordre :** cette étape n'est exécutable qu'APRÈS le merge de **Task 4** (qui ajoute le mode `places` aux `choices` de `run.py` et les args `--cities`/`--budget-eur`). Ne pas la lancer contre du code non migré.

```bash
.venv\Scripts\python.exe -m app.ingestion.run --mode places --budget-eur 10 --cities 100
```
**Coût/durée :** ~100 villes × 2 requêtes (« architecte d'intérieur » / « décorateur d'intérieur ») × ~3 pages max ≈ **jusqu'à ~600 appels × ~0,037 € ≈ 22 €** worst-case (SKU **Text Search Enterprise** = 40 $/1000 = ~0,037 €/appel au palier 0–100k) → le **budget dur 10 €** coupe à ~270 appels facturés, et le **checkpoint de reprise** reprend les villes restantes plus tard. **Crédit gratuit partagé** : depuis mars 2025 Google a remplacé le crédit poolé de 200 $ par des plafonds gratuits PAR SKU — **1 000 appels Enterprise/mois gratuits SEULEMENT**, et ce pool est **PARTAGÉ avec l'enrichissement CHR** (`enrichment/places.py lookup_places` demande le même SKU Enterprise). Un balayage « demain » de ~270–600 appels est donc gratuit UNIQUEMENT si le mois CHR n'a pas déjà consommé le pool — vérifier avant. Contact natif : téléphone 96,7 %, site 100 % (sonde).
**Point de contrôle :** `SELECT count(*) FROM opportunities WHERE source='places'` → **~2 000–4 000** ; retour CLI : `spend_eur <= 10`, `cities_done`, `next_city_index` (reprise).

### 5. (optionnel) Passe contact pour combler les emails du stock — étalé

```bash
.venv\Scripts\python.exe -m app.ingestion.run --mode contact --source sirene_stock   # scrape site -> email/insta ; ne remplit que le vide
.venv\Scripts\python.exe -m app.ingestion.run --mode contact --source places
```

### 6. Contrôles finaux + gates de non-régression

```bash
.venv\Scripts\python.exe -c "from app.database import engine; from sqlmodel import Session, text; print(list(Session(engine).exec(text('SELECT source,population,count(*) FROM opportunities GROUP BY source,population'))))"
# Leads chauds (ne doivent PAS être noyés). NB : après la famille NEUTRE (Task 5), un lead stock frais
# score ~2-3 (fraîcheur detection_date=aujourd'hui), pas 0 ; le split volume/chaud repose sur TIER+labels,
# pas sur un seuil de score brut. Isoler le hot subset par label plutôt que score>=5 :
#   SELECT count(*) FROM opportunities WHERE population='architecte'
#     AND (secondary_signals LIKE '%portfolio hospitality/CHR%' OR secondary_signals LIKE '%jeune studio%');  -> hospitality + récents
#   SELECT count(*) FROM opportunities WHERE population='architecte' AND phone IS NOT NULL;       -> contactables téléphone
.venv\Scripts\python.exe -m app.ingestion.eval.match_eval          # 8/9, 0 faux merge  (NON-RÉGRESSION)
.venv\Scripts\python.exe -m app.ingestion.eval.prescripteurs_run   # gates A1+A2+B (0 faux merge cross-source) OK
# (si OPENAI_API_KEY) .venv\Scripts\python.exe -m app.ingestion.eval.run   # gates CHR intacts
```
**Cible finale :** population `architecte` **~30 000** leads (~28k stock + A1/A2 + Places) ; hot subset (hospitality/récence par label) intact en tête de liste paginée ; **0 faux merge** ; évals CHR/A1/A2 vertes.

---

## Décisions tranchées par la sonde (à lire avant de coder)

**Volet B1 — Stock Sirene**

1. **INSEE (curseur) est la source de volumétrie, PAS recherche-entreprises.** recherche-entreprises plafonne à `page*per_page <= 10000` (400 dur au-delà) ET **sous-compte structurellement d'un facteur ~3** (74.10Z : 98 265 vs **308 629** unités actives INSEE, mesuré live ; ratio confirmé au niveau dept 35 : 1 363 vs 3 265). La pagination **curseur** d'`insee.py` (déjà câblée) fonctionne pour une requête de **stock pur** (sans fenêtre de date) et n'a **aucun plafond** de pagination. → B1 interroge l'endpoint SIRET INSEE, découpé par **département**, le curseur pilotant la pagination **jusqu'à épuisement** (`curseurSuivant == curseur`) ; aucun scindage par CP requis (Paris 75 = **37 291** bruts, curseur jusqu'au bout). *Repro HTTP 200 :* `periode((activitePrincipaleEtablissement:74.10Z OR activitePrincipaleEtablissement:71.11Z) AND etatAdministratifEtablissement:A)` → `header.total = 449569` (deux NAF état=A).
2. **Rendement du filtre mots-clés 74.10Z = MESURÉ.** Sur 300 unités visibles : 9,3 % matchent le filtre ; de ces matchs, **39 % de vrais studios d'intérieur** (11/28), **~50 % ambigus** (« STUDIO X »/« X DESIGN » — cible plausible, à téléphoner), **~11 % faux-amis** (design graphique/produit, fit-out corporate type Cushman & Wakefield). → on RÉUTILISE **tel quel** `jeunes_studios.qualifies` (mêmes mots-clés positifs + gardes négatives `graphique/graphic/graphisme/web/ux/ui/packaging/motion`). Sous doctrine VIDE > FAUX + closers au téléphone, les ambigus restent des leads T3 (fond de liste) ; les faux-amis clairs sont trimés par les gardes négatives.
3. **71.11Z par mots-clés = quasi INUTILISABLE → arm mince, PAS un volume.** Le filtre capte 34 % de 71.11Z mais **0/300 dénominations contiennent « intérieur »** — presque 100 % d'architecture BÂTIMENT (urbanisme/patrimoine). Décision : B1 interroge bien **74.10Z ET 71.11Z** (exigence produit), mais la qualification 71.11Z exige une **co-occurrence STRICTE** `archi*`+`interieur` (ou `decorat*`+`interieur`) dans la dénomination → volume quasi nul MAIS 0 faux-ami bâtiment (VIDE > FAUX). Les vrais architectes d'intérieur en 71.11Z sont captés ailleurs (leur vrai NAF 74.10Z, annuaires CFAI/UFDI, Insta, Places). Documenté et mesuré (0/300).
4. **`[ND]` (non-diffusible) écartés.** Taux mesuré 0 % sur 425 records 74.10Z/71.11Z (contre 65 % côté délta) → le stock est quasi entièrement diffusible. `_best_name` renvoie déjà `None` pour un record masqué → lead droppé (injoignable ET inqualifiable). VIDE > FAUX.
5. **SIREN/dirigeant/ancienneté NATIFS.** Le record INSEE porte `siren`/`siret`/`activitePrincipaleEtablissement`, `dateCreationEtablissement` (→ ancienneté + **booster « création récente <18 mois »**) et `uniteLegale.prenom1/nom` (→ dirigeant pour les personnes physiques). Aucun matcher requis (`siren_match_method='source'`). recherche-entreprises n'expose JAMAIS phone/email/website → enrichissement contact aval reste optionnel.

**Volet B2 — Google Places**

6. **Plafond structurel 60/ville, contact quasi total.** Les 3 villes testées (Paris/Lyon/Rennes, même moyenne ville) saturent le cap Text Search à ~60 résultats (3 pages × 20). Couverture Contact sur 30 fiches : **téléphone 96,7 %, site 100 %, note 100 %**. → 2 variantes de requête par ville (« architecte d'intérieur » + « décorateur d'intérieur ») pour élargir le recall, dédupliquées par `place_id`.
7. **Une seule optimisation d'appel : `maxResultCount=20` + field mask Contact.** Bundler le field mask Enterprise+Contact dans `searchText` avec `maxResultCount=20` (vs `maxResultCount=1` de `lookup_places` aujourd'hui) rend **jusqu'à 20 fiches pleinement enrichies par appel FACTURÉ**. Field mask B2 = `places.id, places.displayName, places.formattedAddress, places.location, places.nationalPhoneNumber, places.websiteUri, places.userRatingCount, places.primaryType` (Contact UNIQUEMENT, pas d'Atmosphere/reviews). **Attention SKU** : demander `nationalPhoneNumber`/`websiteUri`/`userRatingCount` déclenche le SKU **Text Search Enterprise** (120C-BEC3-B48B) = **40 $/1000 = 0,040 $ ≈ 0,037 €/appel** au palier 0–100k (PAS 0,032 €).
8. **Budget & étalement.** Un balayage mensuel top-100 villes (worst 3 pages/ville = ~600 appels ≈ 22 €) dépasse le plafond gratuit → **budget € DUR paramétrable** (arrêt propre quand `spend_eur >= budget_eur`) + **checkpoint mensuel** `{month, next_city_index, spend_this_month}` pour reprendre les villes restantes plus tard. **Crédit gratuit = 1 000 appels Enterprise/mois SEULEMENT** (depuis mars 2025, plus de pool 200 $), **PARTAGÉ avec `lookup_places` (enrichissement CHR)** qui frappe le même SKU Enterprise → une passe « demain » de ~270–600 appels est gratuite UNIQUEMENT si le mois CHR n'a pas déjà brûlé le pool. Dépendance à énoncer explicitement, pas un budget privé renouvelable. *Source :* SKU Text Search Enterprise 40 $/1000, 1 000 gratuits/mois (developers.google.com/maps/billing-and-pricing/pricing).
9. **Places est FORTEMENT ADDITIF (recouvrement ≈ 0).** L'artefact `recouvrement_paris_strict.json` rapporte **7 collisions de tokens naïfs** (token générique unique partagé « interior »/« design »), toutes écartées comme faux positifs de token générique unique — car « architecte »/« intérieur » ne sont pas dans `siret_matcher._GENERIC` (calibré CHR). Sous la dédup de PRODUCTION (égalité de token-SET exacte + ville + corroboration), aucune ne fusionne → **0/59**. → B2 crée quasi que du neuf ; la dédup nom+ville sert surtout à faire **combler le téléphone Places dans les fiches Insta muettes** (exigence 2).
10. **Le gate CHR de `places.py` ne s'applique PAS aux archis.** `lookup_places` filtre `_is_chr_type` (café/resto/hôtel…) → il rejetterait tout studio d'archi. B2 utilise une fonction **NEUVE** `search_places_text` (pas de gate CHR ; garde positive légère sur `displayName` : mot-clé métier présent, faux-ami trimé) → `lookup_places`/`CHR_PLACE_TYPES`/`contact_enricher` CHR **inchangés**. Match `text` (requête ville, pas d'ancrage Sirene) → `contact_confidence='moyenne'`.

**Dédup inter-briques (fondé sur le code lu)**

11. **On généralise le soft-merge A2, on ne le réécrit pas.** `_soft_dedup_architecte`/`_corroborates`/`_merge_corroboration` existent et sont testés (A2). Il suffit d'ÉLARGIR le déclencheur `cand.source == "annuaire"` à `SOFT_DEDUP_SOURCES = {"annuaire", "sirene_stock", "places"}` (3 emplacements dans `_process_candidate`), d'AJOUTER le **téléphone** comme signal de corroboration dans `_corroborates`, et de GÉNÉRALISER la recopie `phone` depuis `cand.raw` (aujourd'hui gardée `== "annuaire"`) à toute source portant un tél dans `raw`. La fusion reste **asymétrique** (jamais déclenchée par un lead Insta/CHR entrant) → A1/A2/CHR bit-à-bit intacts.
    - **CORROBORATION FORTE pour merge inter-sources de masse.** `_corroborates` accepte aujourd'hui un match sur **CP SEUL** (lignes 676-678). Or `sirene_stock`↔`places` n'ont ni l'un ni l'autre de SIREN/Insta commun et `sirene_stock` n'a ni téléphone ni site : un merge stock↔places reposerait sur CP seul → dans un CP dense (75001), deux studios RÉELLEMENT différents avec token-set de nom identique + même ville + même CP seraient faux-mergés (exactement le « faux merge d'homonymes » ciblé par la revue). → **Quand AUCUN des deux côtés n'est annuaire/insta/registre, exiger une corroboration FORTE** (téléphone identique OU domaine site identique) ; le CP-seul (et le dirigeant) ne restent tolérés que si un côté est annuaire/insta avec match de nom de personne. Ajouter une **fixture adverse** (deux studios distincts même nom + même CP) au gate T6 « 0 faux merge ».
    - **PERF — précharger l'index de dédup UNE fois par run.** `_soft_dedup_architecte` fait `select(Opportunity).where(population=='architecte', source != cand.source).all()` (matérialise toutes les lignes des autres sources) puis recalcule `_tokens()`/`_city_tokens()` en Python PAR ligne, PAR candidat — O(N×M). À l'échelle stock (~28k lignes) + passe Places (~4k candidats), c'est ~80M tokenisations + des milliers de SELECT full-table, plus l'autoflush SQLAlchemy à chaque SELECT pendant un batch de 28k inserts. → **Construire une fois par run un index `{(name_tokens, city_tokens) -> [opp]}`** depuis UN SELECT léger (colonnes `id/name/city/website/address/phone/decision_maker` seules, pas d'objets ORM pleins) et faire le lookup en mémoire (ou ajouter une colonne indexée nom+ville normalisés). Ré-estimer les durées runbook après (les ~5 min Places / ~16 min stock supposent cet index en mémoire, PAS le scan O(N×M) naïf).
12. **sirene_stock a un SIREN natif → la fusion SIREN existante le réconcilie « pour rien ».** Un lead `sirene_stock` (SIREN présent) fusionne déjà via la voie `corroborated` (SIREN cross-source) avec une fiche annuaire ayant obtenu le même SIREN par matcher → dédup stock↔annuaire GRATUITE. Contre les fiches Insta (SANS SIREN), c'est la voie douce nom+ville qui opère. Places (sans SIREN) : uniquement voie douce.

**Routage des labels (réutilise A1/A2, aucune nouvelle famille de scoring score-bearing CHR) :**

| source | lifecycle_label | `main_signal` | `secondary_signals` | tier | juge LLM ? |
|---|---|---|---|---|---|
| `sirene_stock` (74.10Z) | `unknown` | `prescripteur actif` *(neutre)* | `stock sirene` (+ `jeune studio (création récente)` si dateCreation < 18 mois) | T3 (+ booster récence) | non |
| `sirene_stock` (71.11Z, co-occ stricte) | `unknown` | `prescripteur actif` *(neutre)* | `stock sirene` | T3 | non |
| `places` | `unknown` | `prescripteur actif` *(neutre)* | `annuaire places` (+ `portfolio hospitality/CHR` si le nom Places contient hôtel/restaurant/CHR) | T3 (T2 si hospitality) | non |

`prescripteur actif` (A1) = famille NEUTRE. **`stock sirene` et `annuaire places` DOIVENT être mappés sur cette même famille neutre dans `SIGNAL_FAMILY` (Task 5)** : sinon, libellés inconnus, `_signal_families` les compte chacun comme une famille distincte → avec `prescripteur actif` cela fait 2 familles → bonus « signaux croisés » +1 parasite (scoring.py ligne 125-127) ; et un lead fraîchement ingéré (detection_date=aujourd'hui) prend +2 « signal très récent » (ligne 81-83). Un simple lead stock scorerait donc ~3, pas 0 — **le score brut ne sépare PAS proprement un plancher « neutre »**. La doctrine d'ORDRE relatif tient (hospitality +2 & croisé, récence +1), mais le tri s'appuie sur **tier + labels explicites**, pas sur un seuil de score brut. `portfolio hospitality/CHR` (A1) = **+2**. `jeune studio (création récente)` devient **score-bearing +1** (nouvelle famille `PRESCRIBER_FRESH`, T5) — jamais émise par un lead CHR → **scores CHR bit-à-bit inchangés**. Re-lancer `prescripteurs_run` après ajout de `PRESCRIBER_FRESH` + famille neutre pour confirmer qu'aucun score A2 `jeunes_studios` ne bascule un gate.

---

### Task 1: `insee` — requête STOCK (curseur, sans fenêtre de date, reprise)

**Modèle d'exécution recommandé : sonnet**

**Files :**
- Modify: `backend/app/ingestion/insee.py` (AJOUTS purs : `build_stock_query`, `fetch_stock_etablissements`)
- Create: `backend/tests/test_insee_stock.py`

**Interfaces :**
- `build_stock_query(naf_codes: Sequence[str], cp_prefixes: Optional[Sequence[str]] = None) -> str` (PURE) : `periode(( <naf OR naf> ) AND etatAdministratifEtablissement:A)` + `(codePostalEtablissement:P*)` si prefixes. **Aucune** clause de date (c'est le stock). Les champs historisés (`activitePrincipaleEtablissement`, `etatAdministratifEtablissement`) DOIVENT être sous `periode(...)` (syntaxe INSEE validée).
- `fetch_stock_etablissements(naf_codes, cp_prefixes=None, limit=0, cursor='*', fetch=None, meta=None) -> Tuple[List[Dict], str]` : boucle curseur (réutilise le pattern de `fetch_new_etablissements` — throttle 2,1 s, `header.statut != 200` → arrêt fail-soft, page vide → arrêt, `curseurSuivant == curseur` → arrêt), `meta['total']` posé sur la 1re page. **`limit` borne les enregistrements BRUTS** (pas les qualifiés — la qualification a lieu en aval dans `map_stock_etablissement`) ; **`limit=0` = illimité → curseur jusqu'à épuisement** (mode par défaut du runbook, un département à la fois : dept 69 ≈ 12k bruts, Paris ≈ 37k, national deux NAF ≈ 450k). **Renvoie `(records, next_cursor)`** — `next_cursor` = dernier `curseurSuivant` (ou `''` si épuisé) pour reprise multi-jours d'un dept géant. `fetch=None` → `_http_get` (défaut réseau), injecté en test. Pas de clé INSEE → `([], '')`.
- **NE PAS toucher** `build_query`/`fetch_new_etablissements` (delta) → `test_sirene_delta`/`test_jeunes_studios` intacts.

**Step 1 — tests (RED)** `test_insee_stock.py` :
```python
"""insee STOCK (B1, T1) — requête sans fenêtre de date, curseur, reprise. Aucun réseau."""
from app.ingestion.insee import build_stock_query, fetch_stock_etablissements

def test_build_stock_query_has_no_date_and_gates_active():
    q = build_stock_query(["74.10Z", "71.11Z"], cp_prefixes=["69"])
    assert "dateCreation" not in q                       # STOCK : pas de fenêtre
    assert "etatAdministratifEtablissement:A" in q
    assert "activitePrincipaleEtablissement:74.10Z" in q
    assert "codePostalEtablissement:69*" in q
    assert q.count("periode(") == 1                       # historisés sous periode()

def test_fetch_stock_paginates_by_cursor_and_returns_next(monkeypatch):
    monkeypatch.setenv("INSEE_API_KEY", "x")
    pages = [  # 2 pages puis épuisement
        {"header": {"statut": 200, "total": 3, "curseurSuivant": "c2"},
         "etablissements": [{"siret": "1"}, {"siret": "2"}]},
        {"header": {"statut": 200, "total": 3, "curseurSuivant": "c2"},  # == curseur -> stop
         "etablissements": [{"siret": "3"}]},
    ]
    calls = {"i": 0}
    def fake(url, params, headers):
        i = calls["i"]; calls["i"] += 1
        assert params["curseur"] in ("*", "c2")
        return pages[min(i, len(pages) - 1)]
    recs, nxt = fetch_stock_etablissements(["74.10Z"], cp_prefixes=["69"], limit=8000, fetch=fake)
    assert [r["siret"] for r in recs] == ["1", "2", "3"]
    assert nxt in ("c2", "")   # curseur de reprise exposé

def test_fetch_stock_no_key_returns_empty(monkeypatch):
    monkeypatch.delenv("INSEE_API_KEY", raising=False)
    assert fetch_stock_etablissements(["74.10Z"], fetch=lambda *a: {}) == ([], "")
```

**Step 2 — RED** : `python -m pytest tests/test_insee_stock.py -q` → `ImportError`.

**Step 3 — impl** : ajouter les 2 fonctions (curseur identique au delta, sans date, tuple de sortie). **Ne rien retirer.**

**Step 4 — GREEN** : `python -m pytest tests/test_insee_stock.py tests/test_sirene_delta.py tests/test_jeunes_studios.py -q` → tout vert (delta intact).

**Step 5 — commit** :
```bash
git add backend/app/ingestion/insee.py backend/tests/test_insee_stock.py
git commit -m "feat(insee): requete stock curseur sans fenetre de date + reprise"
```

---

### Task 2: `SireneStockConnector` (stock 74.10Z+71.11Z, qualification mesurée, booster récence)

**Modèle d'exécution recommandé : sonnet**

**Files :**
- Create: `backend/app/ingestion/sirene_stock.py`
- Create: `backend/tests/test_sirene_stock.py`

**Interfaces :**
- Réutilise `jeunes_studios.qualifies` (74.10Z) et `sirene_delta._best_name`/`_address`/`_ymd`/`_nd`. AJOUTE `qualifies_71` (co-occurrence STRICTE `archi*`+`interieur` OU `decorat*`+`interieur`, PURE, décision #3).
- `STOCK_NAF_CODES = ["74.10Z", "71.11Z"]`. `RECENT_MONTHS = 18` (booster moment).
- `map_stock_etablissement(etab, today) -> Optional[LeadCandidate]` (PURE) : gate état=A ; NAF ∈ STOCK_NAF_CODES ; `_best_name` sinon None (`[ND]`) ; qualification **par NAF** (74.10Z → `qualifies`, 71.11Z → `qualifies_71`) sinon None ; secondary `["stock sirene"]` + `"jeune studio (création récente)"` si `dateCreation` < 18 mois. `source="sirene_stock"`, `population="architecte"`, `lifecycle_label="unknown"`, `main_signal="prescripteur actif"`, `establishment_type="architecte d'intérieur"`, SIREN/SIRET/NAF natifs, `siren_match_method="source"`, `decision_maker` = prénom+nom UL, `activity_start_date=created`, `raw=etab`.
- `SireneStockConnector(Connector)` : `name="sirene_stock"`. `fetch(departments=None, limit=0, cursor='*', since_days=0, since_date=None, max_pages=0, **_)` : **`limit=0` = curseur jusqu'à épuisement** (défaut ; borne des BRUTS, pas des qualifiés). `cp_prefixes` = None si `departments in (None, ['france'])`, `IDF_CP_PREFIXES` si `['idf']`, sinon la liste (préfixes CP → accepte `75` comme `69` comme `75001`). Appelle `fetch_stock_etablissements`, pose `self.last_total_count = meta['total'] or len(records)` et `self.last_cursor` (reprise). Signature compatible avec `run_ingestion` (qui passe `since_days`/`since_date`/`departments`/`max_pages`). `to_candidates` → `map_stock_etablissement`.

**Step 1 — tests (RED)** `test_sirene_stock.py` — fixtures = records INSEE plausibles (extraits du shape delta) :
```python
"""SireneStockConnector (B1, T2). Aucun réseau — records injectés."""
from datetime import date
from app.ingestion.sirene_stock import (
    SireneStockConnector, map_stock_etablissement, qualifies_71,
)

def _etab(siret, naf, denom, etat="A", created="2010-01-01"):
    return {"siret": siret, "siren": siret[:9], "uniteLegale": {"denominationUniteLegale": denom},
            "periodesEtablissement": [{"etatAdministratifEtablissement": etat,
                                       "activitePrincipaleEtablissement": naf}],
            "dateCreationEtablissement": created,
            "adresseEtablissement": {"codePostalEtablissement": "69001",
                                     "libelleCommuneEtablissement": "LYON"}}

def test_7410z_keyword_qualifies():
    c = map_stock_etablissement(_etab("11111111100011", "74.10Z", "ATELIER D INTERIEUR"), date(2026,7,12))
    assert c is not None and c.source == "sirene_stock" and c.population == "architecte"
    assert c.siren == "111111111" and c.siren_match_method == "source"
    assert "stock sirene" in c.secondary_signals

def test_7410z_false_friend_dropped():
    assert map_stock_etablissement(_etab("2","74.10Z","STUDIO DESIGN GRAPHIQUE"), date(2026,7,12)) is None

def test_nd_dropped():
    e = _etab("3","74.10Z","[ND]"); e["uniteLegale"] = {"denominationUniteLegale": "[ND]"}
    assert map_stock_etablissement(e, date(2026,7,12)) is None

def test_recent_booster_under_18_months():
    c = map_stock_etablissement(_etab("4","74.10Z","STUDIO DECO","A", created="2026-01-01"), date(2026,7,12))
    assert "jeune studio (création récente)" in c.secondary_signals   # < 18 mois
    old = map_stock_etablissement(_etab("5","74.10Z","STUDIO DECO","A", created="2010-01-01"), date(2026,7,12))
    assert "jeune studio (création récente)" not in old.secondary_signals

def test_71_11z_requires_strict_cooccurrence():
    assert qualifies_71("CABINET D ARCHITECTURE") is False           # bâtiment
    assert qualifies_71("ARCHITECTE D INTERIEUR MARTIN") is True     # co-occ archi+interieur
    assert map_stock_etablissement(_etab("6","71.11Z","AGENCE D ARCHITECTURE"), date(2026,7,12)) is None

def test_closed_dropped():
    assert map_stock_etablissement(_etab("7","74.10Z","STUDIO DECO", etat="F"), date(2026,7,12)) is None

def test_connector_fetch_sets_cursor_and_total():
    conn = SireneStockConnector()
    def fake_fetch(naf, cp_prefixes=None, limit=0, cursor="*", fetch=None, meta=None):
        if meta is not None: meta["total"] = 42
        return [_etab("8","74.10Z","STUDIO DECO")], "cNEXT"
    import app.ingestion.sirene_stock as m
    m.fetch_stock_etablissements = fake_fetch      # monkeypatch simple
    recs = conn.fetch(departments=["69"], limit=8000)
    assert conn.last_total_count == 42 and conn.last_cursor == "cNEXT"
    assert conn.to_candidates(recs)[0].establishment_name == "STUDIO DECO"
```

**Step 2 — RED** ; **Step 3 — impl** (sibling de `jeunes_studios.py`, réutilise helpers) ; **Step 4 — GREEN** `python -m pytest tests/test_sirene_stock.py -q`.

**Step 5 — commit** :
```bash
git add backend/app/ingestion/sirene_stock.py backend/tests/test_sirene_stock.py
git commit -m "feat(stock): connecteur SireneStock (74.10Z qualifie + 71.11Z co-occ stricte + booster recence)"
```

---

### Task 3: `places` — Text Search 20/appel + `PlacesArchiConnector` (budget €, reprise)

**Modèle d'exécution recommandé : sonnet**

**Files :**
- Modify: `backend/app/ingestion/enrichment/places.py` (AJOUT pur : `search_places_text`, field mask B2 ; **ne touche pas** `lookup_places`/`_match_ok`/`CHR_PLACE_TYPES`)
- Create: `backend/app/ingestion/places_sweep.py` (`PlacesArchiConnector`, checkpoint, garde qualité archi)
- Create: `backend/app/ingestion/data/villes_fr.py` (top communes par population, tuples `(nom, cp, population)` triés desc)
- Create: `backend/tests/test_places_sweep.py`

**Interfaces :**
- `places.search_places_text(query, api_post=None, page_token=None, max_results=20) -> Tuple[List[Dict], Optional[str]]` (PURE-ish) : POST `searchText` avec `ARCHI_FIELD_MASK` (Contact only, décision #7), `regionCode="FR"`, `maxResultCount=max_results`, `pageToken` si fourni. Renvoie `(places, next_page_token)` (chaque place = `{id, name, address, phone, website, rating_count, primary_type}`). `api_post=None` → défaut réseau (une fonction `_post` throttlée 0,2 s) ; injecté en test. Pas de clé → `([], None)`. **Aucun** gate CHR.
- `places_sweep.CityCheckpoint` : lecture/écriture JSON `{month, next_city_index, spend_eur}` (chemin param, défaut `data/places_checkpoint.json`). Reset auto si `month` changé. Tests → chemin temp.
- `EUR_PER_CALL = 0.037` (SKU Text Search Enterprise 40 $/1000 ≈ 0,037 €/appel ; MAJ si le SKU change) ; `QUERIES = ("architecte d'intérieur {ville}", "décorateur d'intérieur {ville}")` ; `_archi_ok(name)` garde positive légère (mot-clé métier présent, faux-ami trimé — réutilise la logique `qualifies`) ; `_hospitality(name)` True si le nom contient hôtel/restaurant/CHR → `portfolio hospitality/CHR`.
- `PlacesArchiConnector(Connector)` : `name="places"`. `fetch(cities=100, budget_eur=10.0, max_pages=3, api_post=None, checkpoint=None, **_)` : reprend au `next_city_index`, pour chaque ville émet les 2 requêtes (jusqu'à `max_pages` pages via `next_page_token`), **incrémente `spend_eur += EUR_PER_CALL` par appel et ARRÊTE dès `spend_eur >= budget_eur`** (fail-soft, checkpoint sauvé), dédup intra-run par `place_id`, garde `_archi_ok`. Pose `self.last_total_count`, `self.spend_eur`, `self.cities_done`, `self.next_city_index`. `to_candidates` → `LeadCandidate(source="places", source_ref=f"places:{place_id}", population="architecte", lifecycle_label="unknown", main_signal="prescripteur actif", establishment_name=name, city, address=formatted, website, secondary_signals=["annuaire places"] + (["portfolio hospitality/CHR"] si hospitality), establishment_type="architecte d'intérieur", detection_date=today, proof_text/proof_url (fiche Google), raw={"phone": phone})`. **Téléphone via `raw['phone']`** (LeadCandidate n'a pas de champ phone — même convention qu'UFDI ; comblé en T4).

**Step 1 — tests (RED)** `test_places_sweep.py` :
```python
"""PlacesArchiConnector (B2, T3). Aucun réseau — api_post injecté, checkpoint temp."""
from app.ingestion.places import search_places_text
from app.ingestion.places_sweep import PlacesArchiConnector, CityCheckpoint

def _place(pid, name, phone="01 02 03 04 05"):
    return {"id": pid, "displayName": {"text": name}, "formattedAddress": f"{name} 75001 Paris",
            "nationalPhoneNumber": phone, "websiteUri": f"https://{pid}.fr",
            "userRatingCount": 12, "primaryType": "interior_designer"}

def test_search_uses_20_and_no_chr_gate(monkeypatch):
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "x")
    seen = {}
    def fake_post(url, headers, json):
        seen.update(json)
        return {"places": [_place("a", "Studio Archi")], "nextPageToken": "T2"}
    places, tok = search_places_text("architecte d'intérieur Paris", api_post=fake_post)
    assert seen["maxResultCount"] == 20 and seen["regionCode"] == "FR"
    assert places[0]["phone"] == "01 02 03 04 05" and tok == "T2"

def test_budget_hard_stop(monkeypatch):
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "x")
    calls = {"n": 0}
    def fake_post(url, headers, json):
        calls["n"] += 1
        return {"places": [_place(f"p{calls['n']}", f"Archi {calls['n']}")], "nextPageToken": None}
    conn = PlacesArchiConnector()
    recs = conn.fetch(cities=100, budget_eur=0.05, max_pages=3, api_post=fake_post,
                      checkpoint=CityCheckpoint(path=tmp_path_json()))
    assert conn.spend_eur <= 0.05 + 1e-9        # budget dur respecté
    assert calls["n"] <= 2                        # coupe vite (0.037/appel)

def test_checkpoint_resumes(monkeypatch):
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "x")
    cp = CityCheckpoint(path=tmp_path_json()); cp.save(next_city_index=5, spend_eur=0.0)
    conn = PlacesArchiConnector()
    def fake_post(url, headers, json):
        return {"places": [], "nextPageToken": None}
    conn.fetch(cities=100, budget_eur=10, api_post=fake_post, checkpoint=cp)
    assert conn.next_city_index >= 5             # reprise au bon endroit

def test_to_candidates_hospitality_and_phone_in_raw(monkeypatch):
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "x")
    conn = PlacesArchiConnector()
    cand = conn.to_candidates([{"place_id": "z", "name": "Deco Hotels & Restaurants",
        "formatted": "10 rue X 75002 Paris", "phone": "06 07 08 09 10",
        "website": "https://z.fr", "hospitality": True}])[0]
    assert cand.source == "places" and cand.source_ref == "places:z"
    assert "portfolio hospitality/CHR" in cand.secondary_signals   # tier T2
    assert cand.raw["phone"] == "06 07 08 09 10" and cand.website == "https://z.fr"
```
*(`tmp_path_json()` = helper local renvoyant un chemin temp `.json` unique ; l'implémenteur le câble via la fixture `tmp_path` pytest.)*

**Step 2 — RED** ; **Step 3 — impl** ; **Step 4 — GREEN** `python -m pytest tests/test_places_sweep.py -q`. Vérifier que `test_enrich_phones.py` (lookup_places CHR) reste vert (aucun impact).

**Step 5 — commit** :
```bash
git add backend/app/ingestion/enrichment/places.py backend/app/ingestion/places_sweep.py backend/app/ingestion/data backend/tests/test_places_sweep.py
git commit -m "feat(places): Text Search 20/appel + PlacesArchiConnector (budget EUR dur + reprise mensuelle)"
```

---

### Task 4: Câblage pipeline — dédup généralisée + `run_places` + CONNECTORS/CLI/endpoints

**Modèle d'exécution recommandé : opus** *(câblage transverse, garde de non-régression match_eval/A2)*

**Files :**
- Modify: `backend/app/ingestion/pipeline.py`
- Modify: `backend/app/ingestion/run.py` (modes `sirene_stock` via `--source`, `places`)
- Modify: `backend/app/main.py` (endpoint `run_places`)
- Modify: `backend/tests/test_run_annuaires.py` (non-régression) ou Create: `backend/tests/test_dedup_cross_source.py`
- Create: `backend/tests/test_run_places.py`

**Changements pipeline (STRICTEMENT ADDITIFS) :**
1. `CONNECTORS += {"sirene_stock": SireneStockConnector, "places": PlacesArchiConnector}` ; `SOURCE_LABELS += {"sirene_stock": "Sirene (stock)", "places": "Google Places"}`.
2. **`SOFT_DEDUP_SOURCES = {"annuaire", "sirene_stock", "places"}`** en constante ; remplacer les 3 `cand.source == "annuaire"` de `_process_candidate` (branche fusion douce + phone-from-raw create + phone-from-raw upsert) par `cand.source in SOFT_DEDUP_SOURCES`. **Note** : la recopie du téléphone depuis `raw` est généralisée à `if cand.raw.get("phone")` (indépendante de la source — CHR/Insta ne posent pas `raw['phone']`, donc inchangés).
3. **`_corroborates` += téléphone ET corroboration FORTE inter-masse** : (a) ajouter une comparaison de téléphone normalisé (`re.sub(r"\D", "", phone)`) entre `cand.raw.get("phone")`/`cand`-porté et `opp.phone` (exigence 2) ; (b) **quand NI `cand` NI `opp` n'est annuaire/insta/registre** (cas `sirene_stock`↔`places`), n'accepter QUE téléphone ou domaine identique — **PAS le CP seul ni le dirigeant** (sinon faux merge d'homonymes même nom + même CP dans un CP dense, cf. Décision #11). Le CP-seul reste toléré uniquement si un côté est annuaire/insta. PURE-ish.
4. **`sirene_stock` : commit PAR candidat, PAS `run_ingestion`.** `run_ingestion` traite tout le batch puis commit UNE fois (ligne 223) ; son `except` appelle `session.rollback()` (ligne 221) qui annule **toute la transaction non commitée** — soit TOUS les candidats déjà insérés du run. À l'échelle stock (10k–28k candidats en un appel), un seul record INSEE malformé (périodes manquantes, date non parsable…) qui lève dans `_process_candidate` **jette tous les inserts précédents du run**. `run_annuaires` commit délibérément par candidat pour cette isolation ; `run_ingestion` non. → **Router `sirene_stock` via un chemin commit-par-candidat** : soit un `run_stock(...)` dédié (miroir de `run_annuaires` : `_process_candidate(...); session.commit()` dans le `try`, `session.rollback()` dans l'`except`), soit adapter la boucle. NE PAS réutiliser la machinerie single-commit de `run_ingestion` pour le stock. `enrich=False`/`is_architecte` court-circuitent l'enricher → aucun coût réseau par candidat.
5. `run_places(cities=100, budget_eur=10.0, max_pages=3, session=None, api_post=None, checkpoint=None) -> IngestStats` : MIROIR de `run_annuaires` — connecteur Places → `_process_candidate` (enricher=None, `is_architecte`) → **commit par candidat**. Propage `stats.total_available`/`stats.soft_merges` ; expose le budget dépensé via un champ ou le log. **`contact_confidence='moyenne'` pour `source='places'`** : `_process_candidate` ne pose aujourd'hui `contact_confidence` que pour `source=='instagram'` (haute) sinon None (lignes 1090-1093) → **ajouter une branche explicite** (dans `_process_candidate` ou le connecteur Places) posant `'moyenne'` pour `places`, sinon la sémantique des décisions #9/#10 reste non tenue.
6. **Préchargement de l'index de dédup** (perf, cf. Décision #11) : construire une fois par `run_stock`/`run_places` un index en mémoire `{(name_tokens, city_tokens) -> [opp]}` depuis un SELECT léger, au lieu du `select(...).all()` full-ORM par candidat de `_soft_dedup_architecte`. Sans ça, la passe stock (~28k) puis Places (~4k) est O(N×M) (~80M tokenisations + autoflush par SELECT) et les durées runbook (~16 min / ~5 min) sont irréalistes.
7. `run.py` : ajouter `"places"` (et `"sirene_stock"` si non routé via window) à la liste `choices` de `--mode` **et** les args `--cities` (défaut 100) / `--budget-eur` (défaut 10.0) dans l'argparse ; `--mode places` → `run_places(cities=args.cities, budget_eur=args.budget_eur)`. `sirene_stock` via `--mode window --source sirene_stock --departments ...` → `run_stock(...)`. (Ces surfaces CLI n'existent PAS encore ; le RUNBOOK step 4 en dépend.)
8. `main.py` : `POST /api/dev/run-places?cities=100&budget_eur=10`.

**Points de vigilance non-régression :**
- La fusion douce reste **asymétrique** : `_soft_dedup_architecte` cherche `source != cand.source` ; le déclencheur est la source ENTRANTE ∈ `SOFT_DEDUP_SOURCES` (jamais `instagram`/`bodacc`/CHR) → `run_prescripteurs`/`run_instagram` bit-à-bit identiques.
- `enrich=False` pour `sirene_stock` **et** `is_architecte` garde l'enricher OFF de toute façon (double sécurité, aucun appel réseau par candidat).
- Commit par candidat pour stock ET places → un record fautif isolé n'annule jamais le run entier.

**Tests clés :**
- `test_run_places.py` : `run_places` sur `api_post` injecté (2 fiches) + `session` mémoire → 2 `Opportunity` `source='places'`, téléphone recopié sur `Opportunity.phone`, **`contact_confidence='moyenne'`** (branche ajoutée en item 5 — asserter explicitement la valeur).
- `test_dedup_cross_source.py` : semer une fiche Insta `population='architecte'` SANS téléphone (nom+ville) ; ingérer un lead `places` MÊME nom+ville + **téléphone + domaine corroborant** → **1 seule fiche**, `stats.soft_merges` contient la paire, **le téléphone Places comble la fiche Insta** (exigence 2). Puis un homonyme `places` MÊME nom+ville SANS corroboration → **2 fiches** (pas de merge). **Fixture adverse inter-masse** : un lead `sirene_stock` et un lead `places` MÊMES tokens de nom + MÊME ville + **MÊME CP mais téléphones/domaines DIFFÉRENTS** (deux studios homonymes distincts) → **2 fiches** (le CP-seul ne suffit PAS entre deux sources de masse, cf. item 3b). Puis vérifier `run_prescripteurs` inchangé (un lead Insta entrant ne déclenche jamais la fusion douce).

**Non-régression obligatoire (dans la tâche) :**
```bash
python -m pytest tests/ -q
python -m app.ingestion.eval.match_eval          # 8/9, 0 faux merge
python -m app.ingestion.eval.prescripteurs_run   # gates A1+A2 OK
```

**Commit :**
```bash
git add backend/app/ingestion/pipeline.py backend/app/ingestion/run.py backend/app/main.py backend/tests/test_run_places.py backend/tests/test_dedup_cross_source.py
git commit -m "feat(pipeline): dedup cross-source generalisee (stock/places) + run_places + corroboration telephone"
```

---

### Task 5: Priorisation — booster récence + tri composite + pagination backend + pager UI

**Modèle d'exécution recommandé : opus** *(scoring sensible + endpoint + frontend, non-régression CHR)*

**Files :**
- Modify: `backend/app/services/scoring.py` (famille `PRESCRIBER_FRESH` +1)
- Modify: `backend/app/models.py` (SIGNAL_TYPES += `"jeune studio (création récente)"`, `"stock sirene"`, `"annuaire places"` — complétude UI, optionnel)
- Modify: `backend/app/routes/opportunities.py` (pagination + tri composite)
- Modify: `backend/app/schemas.py` (si un wrapper de total est préféré — sinon header)
- Modify: `frontend/lib/api.ts`, `frontend/lib/types.ts`, `frontend/app/opportunities/page.tsx` (pager)
- Modify/Create: tests scoring + endpoint pagination

**Scoring (additif, CHR intact) :**
- `PRESCRIBER_FRESH = {"jeune studio (création récente)"}` ; `SIGNAL_FAMILY` += mapping `-> "prescripteur_fresh"` ; dans `compute_score`, `if all_signals & PRESCRIBER_FRESH: points += 1; reasons.append("studio créé récemment (< 18 mois)")`. **Ordre de priorité obtenu** : hospitality `portfolio hospitality/CHR` (+2) > récence (+1) > neutre `prescripteur actif` (0). Ce libellé n'est JAMAIS émis par un lead CHR → `eval.run`/`match_eval` bit-à-bit inchangés (à re-vérifier).
- **Famille NEUTRE pour les labels de volume (OBLIGATOIRE)** : ajouter `"stock sirene"` et `"annuaire places"` à `SIGNAL_FAMILY` mappés sur la famille neutre `"prescripteur"` (ou une famille `"inventaire_archi"` dédiée). Sinon, libellés inconnus, `_signal_families` les compte chacun comme famille distincte → avec `prescripteur actif` = 2 familles → **bonus « signaux croisés » +1 parasite** (scoring.py ligne 121-127). Vérifier ensuite que le split hot/volume ne repose PAS sur un seuil de score brut (un lead stock frais = detection_date aujourd'hui prend déjà +2 « signal très récent », il ne sera jamais à 0) mais sur tier/labels (cf. endpoint + RUNBOOK step 6).
- **Contact complet (téléphone)** : PAS scoré (le téléphone est trop commun pour porter du score) → départage au TRI (ci-dessous), conforme à l'ordre demandé « hospitality > récence > contact > reste ».

**Endpoint liste (pagination + tri composite) :**
- Ajouter `response: Response`, `limit: int = Query(100, ge=1, le=500)`, `offset: int = Query(0, ge=0)`. Calculer `total` via `select(func.count()).select_from(<query filtrée>)` ; poser `response.headers["X-Total-Count"] = str(total)`.
- **Tri par défaut composite** (branche `sort_by == "score"`) : `order_by(Opportunity.opportunity_score.desc(), Opportunity.phone.is_(None).asc(), Opportunity.detection_date.desc())` → hospitality/récents (score haut) en tête, puis à score égal les **contactables téléphone** avant les muets, puis les plus récents. Les autres `sort_by` gardent leur colonne + `.limit(limit).offset(offset)`.
- **Dimensionnement** : 100/page → ~30 000 leads = ~300 pages ; `.all()` remplacé par requête bornée (indispensable à cette échelle). Le `min_score`/`population=architecte`/`source` filtres existants suffisent pour isoler le hot subset en tête.

**Frontend (pager) :**
- `request()` (api.ts) : exposer l'en-tête `X-Total-Count` (retourner `{data, total}` pour `getOpportunities`, ou ajouter `getOpportunitiesPage`). `OpportunityFilters` += `limit?`, `offset?`.
- `opportunities/page.tsx` : état `page`, taille 100, boutons Préc./Suiv. + « N leads au total », `offset = page*100`. Ne casse pas les filtres existants (ils sont passés tels quels).

**Tests :**
- `scoring` : un lead avec `secondary=["jeune studio (création récente)"]` score +1 vs neutre ; un lead CHR (ouverture) score **identique à avant** (aucune régression — assert valeur figée).
- endpoint : 3 fiches, `limit=2&offset=0` → 2 lignes + `X-Total-Count: 3` ; tri composite → à score égal, la fiche avec téléphone d'abord.

**Non-régression obligatoire :**
```bash
python -m pytest tests/ -q
python -m app.ingestion.eval.match_eval
python -m app.ingestion.eval.prescripteurs_run
# (si OPENAI_API_KEY) python -m app.ingestion.eval.run   # gates CHR inchangés
```

**Commit :**
```bash
git add backend/app/services/scoring.py backend/app/models.py backend/app/routes/opportunities.py backend/app/schemas.py frontend/lib/api.ts frontend/lib/types.ts frontend/app/opportunities/page.tsx backend/tests
git commit -m "feat(priorisation): booster recence <18 mois + tri composite (hospitality>recence>tel) + pagination backend"
```

---

### Task 6: Éval élargie 0 faux merge cross-source + run réel borné + docs

**Modèle d'exécution recommandé : opus** *(éval + décision produit + validation live)*

**Files :**
- Modify: `backend/app/ingestion/eval/prescripteurs_metrics.py` (généraliser `false_merges_annuaire_insta` → `false_merges_cross_source`, ou ajouter section)
- Modify: `backend/app/ingestion/eval/prescripteurs_run.py` (le gate `0 faux merge` couvre stock/places)
- Create: fixtures cross-source (fiche Insta muette + lead Places même studio corroboré + homonyme distinct)
- Create: `backend/tests/test_dedup_eval_cross_source.py`
- Create: `docs/b-volume-max-design.md`
- Modify: `C:\Users\Alexis\.claude\projects\c--Users-Alexis-Documents-Projets\memory\MEMORY.md` (index)

**Éval (gate 0 faux merge, alimenté par de VRAIES fusions) :**
- `false_merges_cross_source(pairs, truth_same)` (PURE) : reçoit `stats.soft_merges` (paires réellement fusionnées par le pipeline, toutes sources entrantes ∈ `SOFT_DEDUP_SOURCES`) + l'ensemble annoté `truth_same` (paires « même studio ») → renvoie les fusions NON justifiées. Gate : **liste vide**.
- Section `prescripteurs_run` : mini-jeu offline LIVRÉ — pré-sème (a) une fiche Insta muette + un lead **Places** même studio corroboré (téléphone/domaine) → **fusion attendue** (téléphone Places comble l'Insta), NE DOIT PAS être flaggée ; (b) un homonyme **sirene_stock**/**places** même nom+ville **+ même CP mais tél/domaine différents** SANS corroboration forte → **pas de fusion** (`soft_merges` ne le contient pas). Fait tourner `run_places`/`run_stock(sirene_stock)` sur DB mémoire (`api_post`/`fetch` factices), **collecte `stats.soft_merges`**, calcule le gate. `gates_pass = gate_studio_precision AND gate_zero_hors_cible_in_tiers AND gate_zero_false_merge`. **Jamais court-circuité à `True`.**

**Run réel borné (manuel, hors pytest, SYNCHRONE) — validation live :**
```bash
# stock : un petit département pour valider bout-en-bout (--limit 500 = borne BRUTS pour un run rapide)
.venv\Scripts\python.exe -m app.ingestion.run --mode window --source sirene_stock --departments 35 --limit 500
# places : budget minuscule
.venv\Scripts\python.exe -m app.ingestion.run --mode places --budget-eur 1 --cities 5
```
Ouvrir 8–10 fiches réelles, annoter (`provenance=sirene_stock|places`) dans `architectes_groundtruth.csv`, relancer `prescripteurs_run`.

**Docs `b-volume-max-design.md` :** décisions produit (VOLUME MAX, stock=fond de liste T3, hospitality/récence/téléphone en tête) ; sondes chiffrées (INSEE **308 629** 74.10Z vs 98 265 recherche-entreprises, **449 569** deux NAF état=A ; 74.10Z 9,3 % keyword → ~28 000 qualifiés / 39 % true / gardes négatives ; **71.11Z quasi vide par co-occ stricte, 0/300** ; Places 60/ville, contact 96,7 %/100 %, **~0,037 €/appel** SKU Enterprise, recouvrement 7 collisions génériques → 0/59) ; architecture (stock connecteur commit-par-candidat + places sweep + dédup généralisée avec index préchargé + booster récence) ; **Hors périmètre B** (Apify volume — FREE ~5 $/mois, exclu ; watchlist « nouveau projet » ; scheduling ; génération de messages). Gates : studio_actif ≥ 70 %, 0 hors_cible en tiers, **0 faux merge cross-source** (fixture adverse homonyme même CP incluse), CHR/match_eval intacts.

**Non-régression finale :**
```bash
python -m pytest tests/ -q
python -m app.ingestion.eval.match_eval          # 8/9, 0 faux merge
python -m app.ingestion.eval.prescripteurs_run   # A1+A2+B OK
# (si OPENAI_API_KEY) python -m app.ingestion.eval.run
```

**Commit :**
```bash
git add backend/app/ingestion/eval docs/b-volume-max-design.md backend/tests/test_dedup_eval_cross_source.py
git commit -m "feat(volume): eval 0 faux merge cross-source + run reel borne + docs (B volume max)"
```

---

## Notes de conception (invariants)

- **`match()` CHR / `classify_naf` / `CHR_PLACE_TYPES` jamais touchés** → `match_eval` 8/9 et `eval.run` CHR intacts. B1 n'utilise AUCUN matcher (SIREN natif) ; B2 utilise `search_places_text` NEUF (pas de gate CHR).
- **`run_prescripteurs`/`run_annuaires` (A1/A2) non dégradés** → la fusion douce reste asymétrique (source entrante ∈ `SOFT_DEDUP_SOURCES`, jamais Insta/CHR entrant) ; le gate `0 faux merge` gagne stock/places en couverture, jamais court-circuité.
- **Dédup — trois voies ordonnées, une seule fusion/candidat** (héritées A2, généralisées B) : (1) upsert même-source (`source`+`source_ref`+`population`) ; (2) fusion SIREN cross-source (réconcilie stock↔annuaire) ; (3) fusion douce nom+ville + corroboration (réconcilie stock/places↔Insta faute de SIREN commun ; **téléphone** ajouté comme corroboration ; le téléphone Places comble l'Insta muet). **Corroboration FORTE (téléphone/domaine) exigée quand aucun des deux côtés n'est annuaire/insta/registre** (`sirene_stock`↔`places`) — le CP-seul ne suffit pas là (anti-homonyme même CP). Index nom+ville **préchargé une fois par run** (perf, cf. Task 4 item 6). Chaque voie `return` après fusion → jamais de double comptage.
- **Scores CHR bit-à-bit inchangés** : la seule famille de scoring ajoutée (`PRESCRIBER_FRESH` +1) n'est émise que par des sources architecte → aucun lead CHR ne la porte.
- **VIDE > FAUX absolu** : `[ND]` droppés, faux-amis 74.10Z trimés (gardes négatives), 71.11Z par co-occurrence stricte (volume quasi nul assumé), match Places `text` → `contact_confidence='moyenne'` (jamais surclassé).
- **Coûts** : INSEE gratuit (~16 min France entière stock, ~450 requêtes deux NAF) ; Places budget dur 10 €/run (~0,037 €/appel SKU Enterprise) + reprise mensuelle, **crédit gratuit 1 000 appels Enterprise/mois PARTAGÉ avec `lookup_places` CHR** (à vérifier avant chaque sweep) ; Apify NON utilisé pour le volume (FREE ~5 $/mois).

---

## Notes de revue (2026-07-11)

Revue appliquée sur ce plan. Tous les findings (critical + important + minor) ont été **intégrés au corps du document** ; aucun n'est resté simplement consigné.

- **[critical] `--limit` = bruts, pas qualifiés + volumétrie fausse** → RUNBOOK step 3 réécrit (un dept à la fois, `--limit 0` = curseur-exhaustion), Task 1/Task 2 `limit` documenté comme borne des BRUTS, chiffres corrigés (74.10Z 308 629, deux NAF 449 569, ~450 req × 2,1 s ≈ 16 min, ~28k qualifiés), repro `header.total=449569` ajoutée, checkpoints T1/T2 supprimés au profit du per-dept.
- **[important] `run_ingestion` single-commit fragile à l'échelle stock** → Task 4 item 4 : `sirene_stock` routé via `run_stock` commit-par-candidat (miroir `run_annuaires`), plus via `run_ingestion`. Répercuté Task 6.
- **[important] dédup O(N×M)** → Décision #11 + Task 4 item 6 : index `{(name,city)->[opp]}` préchargé une fois par run, SELECT léger.
- **[important] pricing Places 0,037 (SKU Enterprise) + crédit 1000/mois partagé** → Décisions #7/#8, Task 3 `EUR_PER_CALL=0.037`, RUNBOOK step 4, docs, Notes coûts.
- **[important] score « neutre » miscalibré** → Priorisation + routing table + Task 5 : `stock sirene`/`annuaire places` mappés famille NEUTRE dans `SIGNAL_FAMILY` (anti-bonus croisé), split hot/volume par tier+labels et non par score brut.
- **[important] faux merge CP-seul inter-masse** → Décision #11 + Task 4 item 3b : corroboration FORTE (tél/domaine) exigée quand aucun côté n'est annuaire/insta ; fixture adverse homonyme même CP ajoutée aux gates.
- **[minor] `contact_confidence='moyenne'` non posé** → Task 4 item 5 : branche explicite pour `source='places'`, test asserte la valeur.
- **[minor] Paris 37 291 (pas 22 186) + scindage arrondissement ≠ contrainte API** → RUNBOOK step 3 + Décision #1 corrigés.
- **[minor] recouvrement 7 collisions (pas 0 mesuré)** → Décision #9 + intro reformulées (7 collisions génériques → 0/59 sous token-set+corroboration).
- **[minor] surfaces CLI `--mode places`/`--cities`/`--budget-eur` inexistantes** → RUNBOOK step 4 note la dépendance au merge de Task 4 ; Task 4 item 7 ajoute explicitement les `choices` argparse et les args.
