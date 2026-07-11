# Annuaires + délta jeunes studios (A2) — design

Date : 2026-07-11. Plan d'implémentation : `docs/plans/2026-07-11-a2-annuaires.md`.
Prédécesseur : `docs/population-architectes-design.md` (A1, funnel Instagram archi).

Ce document trace les décisions A2 : ajouter **du VOLUME** à la population
`architecte` (introduite en A1) via deux nouvelles sources de découverte — les
**annuaires professionnels** (CFAI, UFDI) et le **délta Sirene des jeunes studios**
— **en plus** du funnel Instagram A1 et **sans jamais toucher** au CHR ni à ses
évals.

## 1. Décisions produit

- **VOLUME MAX, additif, jamais destructif.** A1 livrait ~44 studios Insta
  (précision 90 %) — trop mince pour prospecter. A2 ajoute un **stock large et
  pré-qualifié** (annuaires) et un **flux de créations très récentes** (délta),
  sans modifier ni le CHR (`run_instagram`, `classify`, gates `recall_opening==1.0`
  / `hot_precision>=0.60` / `match_eval` 8/9 + 0 faux merge) ni A1
  (`run_prescripteurs`, gates `studio_actif_precision>=0.70` / 0 hors_cible en
  tiers). Tous les ajouts sont **strictement additifs**.
- **Annuaires = le STOCK qualifié (source de confiance → pas de juge LLM).** Les
  membres d'un ordre/fédération sont des professionnels du métier par construction.
  Le connecteur pose `lifecycle_label='studio_actif'` directement (économie totale
  de tokens sur le stock). Seule garde déterministe : l'exclusion CFAI « Membre
  Honoraire » (retraité).
- **Délta = le FLUX faible priorité (bruyant, partiellement aveugle).** Un studio
  qui vient de se créer n'a pas encore de fournisseur attitré = meilleure cible
  commerciale, mais le flux INSEE archi est bruyant. Il porte
  `lifecycle_label='unknown'`, **aucun tier attribué** : complément des annuaires,
  pas source principale.
- **Sirene en ENRICHISSEUR des deux.** Les leads annuaire reçoivent leur SIREN via
  un **matcher architecte** (société + ville + domaine du site), ce qui débloque
  dirigeant + ancienneté et la fusion cross-source par SIREN avec le délta. Les
  leads délta portent SIREN/dirigeant/ancienneté **nativement** (données INSEE).

## 2. Résumé des sondes (`.superpowers/sdd/sonde-a2/`)

**Volet annuaires**

- **CFAI = HTML statique pur, pagination GET `?page=N`.** `curl` brut == navigateur
  (aucun JS). Liste : `table.table-list > tbody > tr`, 5 `<td>` ; **15 lignes/page,
  50 pages, 738 total** (badge « 738 résultats »). Fiche : `<h1>`, `member-company`,
  `member-activity`, adresse/téléphone/**email `mailto:`**/site en clair. Robots CFAI
  **permissif** (aucun Disallow). → `requests` + `BeautifulSoup`, aucun Playwright.
- **CFAI — filtre honoraire.** Un adhérent marqué `member-activity-summary`
  « Membre Honoraire du CFAI » = retraité, sans société ni contact → **écarté**
  (`parse_fiche` renvoie `None`). Sur l'échantillon sondé (adhérents 12/14/16/17/19/
  21), 5 cibles + 1 seul honoraire (17).
- **CFAI — le filtre spécialité Hôtellerie/Restauration est côté SERVEUR** (POST +
  CSRF), invisible sur la fiche, et **LARGE** (339/738 = 46 %, tag auto-déclaré peu
  discriminant). → **NON utilisé** comme filtre principal ; CFAI → **tier T3** par
  défaut (le signal hospitality CFAI est trop bruité pour porter un T2).
- **UFDI = le plus simple.** WordPress/Divi statique. Découverte par la page
  nationale `/decorateur/decorateurs-france-fr.html` : **~157 profils réels en UN
  fetch** (vérifié en rejouant `parse_list_page` : 157 cartes `div.et_pb_team_member`).
  ⚠️ **PAS ~255** : la page contient aussi ~98 liens de NAVIGATION départementale qui
  matchent le motif slug mais **ne sont pas des cartes** `team_member` — le parseur
  les exclut par le scope carte (157 + 98 = 255 ; le bon volume produit est **~157**).
  Fiche : **téléphone en clair via `data-numero`** (aucun JS malgré l'UI « cliquer
  pour afficher »), site via `a.site`, réseaux (Instagram), et **liste d'activités
  structurée** `<li>Décoration Hôtels/Restaurants</li>`. **PAS d'email en clair.**
  Robots UFDI : `/decorateur/*.html` **Allow**, `/membres.php` **Disallow** → on
  n'utilise QUE `/decorateur/*`.
- **UFDI — signal hospitality NATIF → tier T2 sans juge.** Une fiche portant
  `Décoration Hôtels` ou `Décoration Restaurants` reçoit le libellé A1
  `portfolio hospitality/CHR` (secondary, +2) → **tier T2** ; sinon T3. Vérifié sur
  les profils sondés : Kokocinski / Boé de Pirey / Béatrice Elisabeth / Schleifer
  portent le tag hospitality (T2) ; Benedetti sans tag → T3, correctement.

**Volet délta jeunes studios**

- **Flux RECALL-ORIENTÉ mais BRUYANT et PARTIELLEMENT AVEUGLE.** Mesuré sur 30 j
  France (NAF 71.11Z/74.10Z) : **1625 créations / 30 j (~54/j)**, dont **91 %
  d'entrepreneurs individuels** (le filtre catégorie juridique éliminerait 91 % ET
  de vrais studios solo → inutilisable seul) et **65 % de dénominations MASQUÉES**
  (`[ND]`, non-diffusion → structurellement invisibles à tout filtre mots-clés).
- **Filtre mots-clés dénomination — rendement MESURÉ.** Sur les dénominations
  visibles (35 % du flux) : **28,2 % retenus** (147/522). Rapporté au flux total :
  **9,8 %** (~5 créations qualifiables/jour sur ~54 brutes). Mots-clés :
  `interieur, design, studio, agencement, deco, archi, atelier, concept, home,
  espace`. **Faux positifs** : `DESIGN GRAPHIQUE`/graphisme/web/UX (le NAF 74.10Z
  couvre le design graphique/produit, pas que l'intérieur) → **garde négatif**
  (`graphique/graphic/graphisme/web/ux/ui/packaging/motion`). Dénomination masquée
  `[ND]`/vide → **sautée** (injoignable ET inqualifiable).
- **Ancienneté / dirigeant NATIFS.** Le record INSEE porte
  `dateCreationEtablissement` (→ ancienneté) et `uniteLegale.prenom1/nom` (→
  dirigeant pour les personnes physiques). Le délta n'a **pas besoin du matcher**.

**Houzz — REPORTÉ (hors périmètre A2).** Anti-bot actif **non-déterministe**
(« Client Challenge » déclenché même en navigateur réel), SPA React ~2 Mo/page, ToS
incertain (SaaS pro, pas annuaire public). Sonde verbatim : « Non recommandé comme
source principale… ne pas bâtir de pipeline automatisé dessus sans réévaluation
(légale + technique) ». → **AUCUN connecteur Houzz**, aucun Playwright. Report vers
un éventuel A2bis manuel / API partenaire Houzz Pro.

## 3. Architecture (delta vs A1, tout ADDITIF)

- **Connecteurs annuaire** (`annuaires/cfai.py`, `annuaires/ufdi.py`) : interface
  `Connector` (`fetch`/`to_candidates`), parsing **PUR** (fixtures = extraits des
  HTML sondés, aucun réseau en test), HTTP injectable (`annuaires/http.py :
  HtmlFetch`, `polite_get` = throttle 2,5 s, User-Agent honnête, fail-soft). Émettent
  `LeadCandidate(population='architecte', source='annuaire',
  lifecycle_label='studio_actif')`.
- **Connecteur délta** (`jeunes_studios.py`) : sibling de `SireneDeltaConnector`,
  réutilise `insee.fetch_new_etablissements` (throttle 2,1 s intégré), NAF archi,
  `qualifies()` (filtre mots-clés + garde négatif), mapping natif SIREN/dirigeant/
  ancienneté. Émet `source='jeunes_studios', lifecycle_label='unknown'`.
- **Matcher architecte** (`siret_matcher.match_architecte`) : chemin **PARALLÈLE** au
  `match()` CHR (jamais modifié → `match_eval` 8/9, 0 faux merge intacts), gate NAF
  71.11Z/74.10Z (`classify_naf_prescripteur`, séparé de `classify_naf`), nom + ville
  + domaine, arbitre LLM réutilisé, **sans étage adresse** (les studios sont souvent
  des bureaux à domicile).
- **Orchestration annuaire** (`pipeline.run_annuaires`) : miroir de
  `run_prescripteurs` — connecteur → enrichissement SIREN archi (dirigeant +
  ancienneté via `SireneEnricher.lookup` + `dirigeant_from_result`) → **fusion douce
  nom+ville** → `_process_candidate` (branche `population='architecte'` de A1,
  contourne le classifieur CHR). Commit par candidat (isolation), fail-soft.
- **Délta** : passe par `run_ingestion(source='jeunes_studios')` (machinerie
  window/incremental existante) ; les candidats portent déjà leur SIREN natif →
  `_process_candidate` les persiste sans ré-enrichir.

### Déduplication — trois voies ordonnées, une seule fusion par candidat

`_process_candidate` tente, dans l'ordre, et `return` après la première fusion :

1. **Upsert même-source** (`source` + `source_ref` + `population`).
2. **Fusion SIREN cross-source** (`corroborated`, existante) : réconcilie
   annuaire↔délta dès que le matcher a trouvé le SIREN.
3. **Fusion douce nom+ville** (`_soft_dedup_architecte`, annuaire ENTRANT seulement) :
   réconcilie annuaire↔Instagram faute de SIREN commun (les studios Insta A1 n'ont
   pas de SIREN, matcher A1 CHR-gated). **Asymétrique** (seul un `cand.source ==
   'annuaire'` la déclenche) → `run_prescripteurs` A1 reste **bit-à-bit identique**.

**Anti-homonyme fortuit (finding de revue).** Nom+ville identiques sont **NÉCESSAIRES
mais PAS SUFFISANTS** : la voie (3) exige **en plus** une corroboration
(`_corroborates` : même domaine de site, même code postal, ou même dirigeant
normalisé). **Exactement 1** fiche corroborée → fusion ; **0, ≥2, ou aucune
corroboration** → rien (doctrine **VIDE > FAUX** : deux fiches valent mieux qu'un
faux merge). Chaque fusion douce est tracée dans `IngestStats.soft_merges` (paire
`(ref_annuaire, ref_insta)`), ce qui alimente le gate 0 faux merge de l'éval sur des
fusions **RÉELLES**.

### Routage des labels (réutilise A1, aucune nouvelle famille de scoring)

| source | lifecycle | `main_signal` | `secondary_signals` | tier | juge |
|---|---|---|---|---|---|
| `annuaire` (CFAI) | `studio_actif` | `prescripteur actif` *(neutre)* | `annuaire cfai` | T3 | non |
| `annuaire` (UFDI, hospitality) | `studio_actif` | `prescripteur actif` | `annuaire ufdi`, `portfolio hospitality/CHR` | **T2** | non |
| `annuaire` (UFDI, sans) | `studio_actif` | `prescripteur actif` | `annuaire ufdi` | T3 | non |
| `jeunes_studios` (délta) | `unknown` | `prescripteur actif` | `jeune studio (création récente)` | — | non |

`prescripteur actif` (A1) = famille de scoring **NEUTRE**. `portfolio hospitality/CHR`
(A1) = famille +2. **Aucun nouveau libellé score-bearing → scores CHR bit-à-bit
identiques.** Le tag `CORROBORATION_TAG` (« corroboré registre × instagram », +1) est
**exclu quand la source est `annuaire`** (un annuaire n'est pas un registre ; le
libellé serait sémantiquement faux et le +1 injustifié pour la population archi).

## 4. Évaluation & gates (A2)

Ground-truth : `backend/app/ingestion/eval/architectes_groundtruth.csv` étendu de
cas annuaire/délta réels (annotés depuis les HTML/records sondés) —
`provenance ∈ {annuaire_cfai, annuaire_ufdi, delta_insee}`, `handle` = identifiant
lead (`cfai:<id>`, `ufdi:<slug>`, `siret:<siret>`). Les membres CFAI/UFDI =
`studio_actif` par construction ; un honoraire CFAI ou un faux positif délta
(garde négatif) = `hors_cible`.

Gates (`app.ingestion.eval.prescripteurs_run`, **tous durs**, jamais affaiblis) :

- **`studio_actif_precision >= 0.70`** (A1, inchangé) ;
- **0 hors_cible en tiers T1/T2** (A1, inchangé) ;
- **0 faux merge annuaire×insta** (A2, nouveau) :
  `prescripteurs_metrics.false_merges_annuaire_insta(pairs, truth_same_studio)`
  consomme les paires **RÉELLEMENT fusionnées** par le pipeline
  (`stats.soft_merges`) et renvoie celles NON annotées « même studio » ; doit être
  vide. Le gate tourne **de bout en bout** sur un mini-jeu **LIVRÉ**
  (`eval/annuaires_snapshots/` : fixtures HTML CFAI + un record INSEE de délta) via
  `run_annuaires_gate()`, avec matcher/sirene déterministes injectés. Le mini-jeu
  exerce la métrique : (a) un couple annuaire×insta **légitime** (même studio,
  corroboré par le domaine de site → fusion attendue, à NE PAS flagger) + (b) un
  homonyme **distinct** même nom+ville sans corroboration (à NE PAS fusionner). **Le
  gate n'est jamais court-circuité à `True` faute de données.**
- **≥ 70 % des membres annuaire → `studio_actif`** (honoraires CFAI écartés en amont
  par `parse_fiche` → non comptés).

`gates_pass = gate_studio_precision AND gate_zero_hors_cible_in_tiers AND
gate_zero_false_merge AND gate_annuaire_studio_actif`.

**Non-régression obligatoire** : `match_eval` (8/9, 0 faux merge) et l'éval CHR
(`recall_opening==1.0`, `hot_precision>=0.60`) restent inchangés — le `match()` CHR
et `run_prescripteurs` A1 ne sont jamais modifiés.

Tests unitaires (aucun réseau, aucun LLM) :
`tests/test_cfai_connector.py`, `tests/test_ufdi_connector.py`,
`tests/test_jeunes_studios.py`, `tests/test_match_architecte.py`,
`tests/test_run_annuaires.py`, `tests/test_annuaires_eval.py`.

### Commandes du run réel borné (manuel, Livraison — coût réseau assumé, scraping poli)

```
# Annuaires (UFDI d'abord : le plus simple, 1 page + fiches ≈ throttle 2,5 s/fiche)
python -m app.ingestion.run --mode annuaires --annuaire ufdi --limit 30
python -m app.ingestion.run --mode annuaires --annuaire cfai --limit 30
# Délta jeunes studios (nécessite INSEE_API_KEY ; throttle 2,1 s intégré)
python -m app.ingestion.run --mode window --source jeunes_studios --since 30 --limit 100
# Éval + gates
python -m app.ingestion.eval.match_eval            # non-régression : 8/9, 0 faux merge
python -m app.ingestion.eval.prescripteurs_run     # gates A1 + A2 (dont 0 faux merge)
```

Après les runs : **passe d'annotation navigateur** — ouvrir 8-10 fiches réelles
issues des runs, annoter dans `architectes_groundtruth.csv`
(`provenance=annuaire_*|delta_insee`), puis relancer `prescripteurs_run`. Le gate
offline reste autonome ; l'annotation ÉTEND le GT au-delà des fixtures livrées.

## 5. Pas d'auto-purge annuaire (asymétrie assumée)

`run_annuaires` contourne volontairement `verdict_cache` / `PRESCRIBER_ROUTING` : le
connecteur pose `lifecycle_label='studio_actif'` / `main_signal='prescripteur actif'`
directement (source de confiance). **Un lead annuaire ne cache donc AUCUN verdict.**

Conséquence assumée : les fiches `source='annuaire'` ne passent **jamais** par
`_purge_requalified` → un membre retiré ensuite du CFAI/UFDI **n'est pas désactivé
automatiquement** (asymétrie avec la requalification Instagram, qui elle re-juge et
peut purger). Acceptable en A2 : stock stable, faible churn d'un ordre/fédération. Un
**balayage stale-annuaire** (revisite périodique + désactivation des refs disparues)
est renvoyé à **A3**.

## 6. Hors périmètre A2 (à ne PAS implémenter ici)

- **Houzz automatisé** : anti-bot dur + ToS incertain (sonde). Reporté (A2bis manuel
  / API partenaire). Aucun connecteur, aucun Playwright.
- **Filtre spécialité CFAI côté serveur** (POST + CSRF Hôtellerie/Restauration) :
  trop large (46 %) et bruité pour porter un tier ; on pagine le stock en GET simple.
- **Watchlist « nouveau projet »** (booster T1 dynamique par re-visite) → **A3**.
- **Balayage stale-annuaire** (désactivation des fiches annuaire disparues) → **A3**.
- **Scheduling / cron** des runs annuaire/délta → hors A2 (runs manuels bornés).
- **Génération de messages** spécialisés prescripteurs → réutilise l'existant.
- **Élargir le `match()` CHR** au NAF archi → on ajoute un chemin parallèle
  (`match_architecte`), on ne modifie PAS le matcher CHR (préserve `match_eval`).
