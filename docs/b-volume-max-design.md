# B — Volume max (stock Sirene + balayage Google Places) — Design

> Fait passer la population `architecte` de ~1 000 leads (A1 Insta + A2 annuaires
> + délta) à **~30 000 leads qualifiables** via deux connecteurs de découverte de
> masse ADDITIFS, sans jamais toucher au CHR ni à ses évals. Plan d'exécution :
> `docs/plans/2026-07-12-volume-max.md` (autorité : « Notes de revue 2026-07-11 »).

## Décisions produit

- **VOLUME MAX, mais le volume ne noie pas les leads chauds.** Ordre de priorité
  imposé au tri : **hospitality (`portfolio hospitality/CHR`, +2)** > **création
  récente (< 18 mois, booster « moment », +1)** > **contact complet (téléphone,
  départage au TRI)** > reste. Le stock brut est **fond de liste (T3)** : cible
  plausible à téléphoner, jamais surclassée.
- **VIDE > FAUX absolu.** `[ND]` (dénomination masquée) écartés ; faux-amis 74.10Z
  trimés par gardes négatives (`graphique/graphic/web/ux/ui/packaging/motion`) ;
  71.11Z qualifié par **co-occurrence STRICTE** `archi*/decorat*` + `interieur`
  (volume quasi nul assumé, 0 faux-ami bâtiment) ; match Places `text` ->
  `contact_confidence='moyenne'` (jamais surclassé).
- **Split hot/volume par TIER + LABELS, PAS par seuil de score brut.** Un lead
  stock fraîchement ingéré (`detection_date`=aujourd'hui) prend déjà +2 « signal
  très récent » : le score brut ne sépare pas proprement un plancher neutre. On
  s'appuie sur `tier` + labels explicites (`portfolio hospitality/CHR`, `jeune
  studio (création récente)`), cf. RUNBOOK step 6 du plan.

## Sondes chiffrées (`.superpowers/sdd/sonde-volume/`)

- **Stock INSEE (curseur) = source de volumétrie.** 74.10Z état=A = **308 629**
  unités INSEE (vs 98 265 côté recherche-entreprises, sous-comptage ~×3) ; deux
  NAF (74.10Z + 71.11Z) état=A = **449 569** (repro HTTP 200 :
  `periode((activitePrincipaleEtablissement:74.10Z OR ...71.11Z) AND
  etatAdministratifEtablissement:A)` -> `header.total=449569`). Pagination curseur
  SANS plafond, jusqu'à épuisement (`curseurSuivant == curseur`).
- **Rendement filtre mots-clés 74.10Z = 9,3 %** du stock -> **~28 000 qualifiés**.
  Parmi les qualifiés : **39 % vrais studios d'intérieur** (11/28), ~50 % ambigus
  (cible plausible), ~11 % faux-amis clairs (trimés par gardes négatives).
- **71.11Z quasi vide par co-occurrence stricte** : 0/300 dénominations 71.11Z
  contiennent « intérieur » -> arm mince, pas un volume (VIDE > FAUX).
- **Places** : plafond structurel ~60 fiches/ville (3 pages × 20) ; contact natif
  **téléphone 96,7 %, site 100 %** ; **~0,037 €/appel** (SKU Text Search
  Enterprise 40 $/1000, palier 0-100k) ; **crédit gratuit 1 000 appels
  Enterprise/mois PARTAGÉ avec `lookup_places` CHR** (vérifier avant chaque sweep).
- **Recouvrement Places ≈ 0** : 7 collisions de tokens génériques (« interior »/
  « design »), toutes écartées sous égalité de token-SET exact + ville +
  corroboration -> **0/59**. Places crée quasi que du neuf ; la dédup sert surtout
  à **combler le téléphone Places dans les fiches Insta muettes** (exigence 2).

## Architecture (delta A1/A2, tout ADDITIF)

- **`insee.build_stock_query` / `fetch_stock_etablissements`** (T1) : requête stock
  SANS fenêtre de date, curseur, `next_cursor` exposé pour reprise multi-jours.
  Les fonctions delta ne sont pas modifiées.
- **`SireneStockConnector`** (T2, `source='sirene_stock'`) : 74.10Z via
  `jeunes_studios.qualifies`, 71.11Z via `qualifies_71` (co-occ stricte), booster
  `jeune studio (création récente)` si `dateCreation` < 18 mois, SIREN/dirigeant/
  ancienneté NATIFS (`siren_match_method='source'`).
- **`places.search_places_text`** (T3) : Text Search (New), field mask Contact,
  `maxResultCount=20`, AUCUN gate CHR (`lookup_places`/`CHR_PLACE_TYPES` intacts).
  **`PlacesArchiConnector`** + `data/villes_fr.py` + `CityCheckpoint` : budget €
  DUR (`EUR_PER_CALL=0.037`), reprise mensuelle, garde positive `_archi_ok`.
- **`pipeline`** (T4) : `run_stock`/`run_places` **commit PAR candidat** (miroir de
  `run_annuaires` — un record fautif isolé n'annule jamais les inserts précédents,
  contrairement au single-commit de `run_ingestion` fatal à l'échelle ~28k) ;
  **index de dédup préchargé UNE fois par run** (`_build_dedup_index`, SELECT léger
  -> lookup O(1), au lieu du O(N×M) scan full-ORM par candidat).
- **`scoring`** (T5) : famille `PRESCRIBER_FRESH` (+1, jamais émise par un lead CHR
  -> scores CHR bit-à-bit inchangés) ; `stock sirene`/`annuaire places` mappés sur
  une famille NEUTRE dans `SIGNAL_FAMILY` (anti-bonus « signaux croisés » parasite).
  Endpoint liste **paginé** (`limit`/`offset` + `X-Total-Count`) + tri composite
  `score desc, téléphone présent desc, detection_date desc`.

## Dédup cross-source — trois voies ordonnées, une seule fusion/candidat

Généralise le soft-merge A2 (source ENTRANTE ∈ `SOFT_DEDUP_SOURCES = {annuaire,
sirene_stock, places}`), **asymétrique** (jamais déclenché par un lead Insta/CHR
entrant -> A1/A2/CHR bit-à-bit intacts) :

1. **upsert même-source** (`source` + `source_ref` + `population`) ;
2. **fusion SIREN cross-source** (réconcilie stock↔annuaire « pour rien ») ;
3. **fusion douce nom+ville + corroboration** (`_corroborates`) : téléphone
   normalisé (`raw['phone']`/`opp.phone`, exigence 2) OU domaine propre identique.
   **CORROBORATION FORTE (tél/domaine) EXIGÉE quand aucun des deux côtés n'est
   annuaire/insta/registre** (`sirene_stock`↔`places` ∈ `MASS_SOURCES`) : le géo
   seul (numéro de voie, dirigeant) NE corrobore PAS entre deux sources de masse
   (deux studios homonymes d'un CP dense fusionneraient à tort). Le CP seul est
   d'ailleurs redondant avec l'égalité de ville imposée par la fusion douce.

## Éval — gate 0 faux merge CROSS-SOURCE (T6)

- **`false_merges_cross_source(pairs, truth_same_studio)`** (PURE, généralise
  l'ancien `false_merges_annuaire_insta`, dont le nom reste un alias rétro-compat) :
  reçoit les paires RÉELLEMENT fusionnées (`stats.soft_merges`) + l'ensemble annoté
  « même studio » -> renvoie les fusions non justifiées. **Gate : liste vide.** Ne
  mesure QUE les fusions émises (une non-fusion ne peut pas être un faux merge) ;
  le rappel est laissé à la doctrine VIDE > FAUX.
- **`prescripteurs_run.run_cross_source_gate()`** : mini-jeu offline LIVRÉ,
  autonome (api_post/connector factices, DB mémoire, aucun réseau ni LLM). Exerce
  RÉELLEMENT `run_stock` + `run_places` et consomme `stats.soft_merges` — **jamais
  court-circuité à True** :
  - (a) fiche Insta MUETTE (« Atelier Lumen », Paris, sans téléphone) + lead
    **Places** même studio corroboré par le domaine -> **fusion attendue** (le
    tél/domaine Places comble l'Insta), annotée « même studio » -> pas flaggée ;
  - (b) **fixture adverse inter-masse** : lead `sirene_stock` + lead `places`
    homonymes (« Studio Meridien », même ville + **même CP 75001**, tél/domaines
    DIFFÉRENTS) -> pas de corroboration forte -> **ne fusionne pas**. Un faux merge
    apparaîtrait dans `soft_merges` hors vérité -> gate ROUGE.
  Le gate global `gate_zero_false_merge` = conjonction annuaire×insta (A2) **ET**
  cross-source de masse (B). `gates_pass` inchangé par ailleurs.

## Échantillonneur GT stock (exigence propriétaire — le « Tir »)

`app.ingestion.eval.stock_gt_sample` — MESURE la précision réelle du stock en prod :

1. **`--sample N --out CSV`** : tire N leads `sirene_stock` (population architecte)
   AU HASARD de la base (`sample_stock_leads`, graine RNG paramétrable), écrit un
   CSV `handle,denomination,siren,ville,label` (`label` VIDE à annoter).
2. Annotation manuelle : `cible` (vrai studio d'intérieur OU ambigu plausible à
   contacter) | `hors_cible` (faux-ami clair) | VIDE (ignoré).
3. **`--score CSV --min-precision X`** : `stock_precision` = `cible / (cible +
   hors_cible)` sur les seules lignes annotées, **gate paramétrable** (défaut 0,70),
   exit 0/1. Servira au **Tir (N=100)**.

`sample_stock_leads`/`stock_precision` sont PURES (session/lignes injectées),
testées sans base réelle ni réseau.

## Coûts

- **INSEE gratuit** : ~450 requêtes deux NAF × 2,1 s ≈ **16 min** pour la France
  entière (~28k qualifiés retenus). Clé INSEE gratuite.
- **Places** : budget € DUR **10 €/run** (~0,037 €/appel SKU Enterprise) + reprise
  mensuelle ; crédit gratuit **1 000 appels/mois PARTAGÉ avec `lookup_places` CHR**
  (à vérifier avant chaque sweep). Arrêt propre dès `spend_eur >= budget_eur`.

## Hors périmètre B (assumé)

Apify volume (FREE ~5 $/mois, exclu) ; watchlist « nouveau projet » ; scheduling ;
génération de messages. Ces briques ne sont PAS couvertes par ce lot.

## Gates de non-régression (invariants)

- `match_eval` (CHR) : 0 faux merge, exit 0 — `match()`/`classify_naf`/
  `CHR_PLACE_TYPES` jamais touchés.
- `prescripteurs_run` : studio_actif ≥ 70 %, 0 hors_cible en tiers, **0 faux merge
  cross-source** (fixture adverse homonyme même CP incluse), membres annuaire ≥
  70 % studio_actif.
- `eval.run` (CHR, si OPENAI_API_KEY) : gates CHR intacts (aucune famille de scoring
  score-bearing ajoutée côté CHR).
