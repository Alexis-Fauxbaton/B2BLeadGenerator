# Population « architectes d'intérieur » (A1) — design

Date : 2026-07-10. Plan d'implémentation : `docs/plans/2026-07-10-population-architectes.md`.

## 1. Décisions produit

- **Pivot / nouvelle population, pas un remplacement du CHR.** Le classifieur CHR
  (`app.ingestion.classify`) et son funnel (`run_instagram`) restent **intacts, bit-à-bit**
  (gates `recall_opening == 1.0`, `hot_precision >= 0.60`, `match_eval` 8/9+, 0 faux merge).
  Les architectes d'intérieur sont un **flux parallèle** : `run_prescripteurs`, une colonne
  `Opportunity.population` (`'chr'` par défaut, `'architecte'` pour ce flux), et un contournement
  ciblé du classifieur CHR dans `_process_candidate` (les archis ne passent jamais par les règles
  CHR qui les auraient droppés comme non-lieu).
- **VOLUME MAX national, PAS de filtre géographique IdF, PAS de filtre CHR à la découverte.**
  Les hashtags d'architecture d'intérieur sont nationaux (Sables-d'Olonne, Bordeaux, Compiègne,
  Pays Basque…) ; appliquer un filtre IdF ou un filtre CHR à `discover_prescripteurs` réduirait
  artificiellement le volume sans bénéfice (l'objectif business est la couverture nationale des
  studios d'architecture d'intérieur, futurs prescripteurs de projets CHR).
- **Le tier est un booster de priorité, PAS un filtre.** Tout `studio_actif` devient un lead, quel
  que soit son tier (T1/T2/T3). Le tier ne change QUE l'accroche et le score (via des libellés
  `secondary_signals` additifs) — jamais l'éligibilité au statut de lead. `studio_dormant` reste
  aussi un lead (bas de pile), `compte_perso`/`hors_cible`/`noise` ne deviennent jamais des leads
  (verdict caché en cache uniquement).

## 2. Résumé de la sonde (15 comptes réels annotés)

La sonde (`.superpowers/sdd/sonde-architectes-profils/`, seed de
`backend/app/ingestion/eval/architectes_groundtruth.csv`) a tranché la conception avant le code :

**3 hashtags mesurés** (mesure directe, pas de recouvrement inter-tags significatif → les combiner
multiplie la couverture au lieu de la doubler) :
- `#architectedinterieur` — 70 % de comptes distincts pertinents
- `#architecturedinterieure` — 70 %
- `#agencement` — 80 % de comptes distincts, mais le plus large (~50 % d'artisans dans
  l'échantillon) : ratissé large à la découverte, le garde/juge trie la précision en aval.

Les deux premiers tags sont **contigus** (sans espace) dans l'usage réel Instagram — un piège de
conception documenté ci-dessous (§5, correction Task 2).

**8 motifs discriminants** identifiés par la sonde :
1. Le titre exact « architecte d'intérieur » en bio est le signal le plus fort **mais insuffisant
   seul** (porté aussi bien par un `compte_perso` que par un `hors_cible`) → pas de garde dur
   `studio_actif`, le juge tranche.
2. `hors_cible` déterministe sûr : formation/coaching (`coach`, `cours privé(s)`, `formation`,
   `masterclass`, `mentorat`).
3. `hors_cible` déterministe sûr : artisan/fabricant voisin (`menuiserie`/`ébéniste`/`tapissier`/
   `serrurier`/`marbrier`) **sans** titre archi en bio (le titre archi neutralise ce garde).
4. `hors_cible` déterministe sûr : non-prescripteur (`graphiste`/`webdesign`/`community manager`/
   `photographe`/`webmagazine`/`UX-UI`).
5. `hors_cible` léger : domaines étrangers (`.be`/`.ch`/`.ca`) — piège CHR connu, aucun cas dans
   l'échantillon archi mais garde conservé par cohérence.
6. Récence ambiguë (âge du dernier post + cadence sur 90 jours) : précalculée en code, jamais
   tranchée par un garde dur — c'est le juge qui décide `studio_actif` vs `studio_dormant`.
7. Email systématiquement absent des champs structurés Instagram (`businessEmail`/`public_email`
   = `None` sur 15/15 comptes sondés) → toujours parsé depuis bio/posts ; domaine propre = signal
   pro fort, `@gmail.com` penche `compte_perso`.
8. Preuve « hospitality » réelle (tier T2) : collaboration explicite avec un compte CHR
   (`@hotel_restaurant_locean`) ou pôle commercial/retail dédié (« BIFUR COMMERCE ») → booléen
   `hospitality_proof` extrait par le juge.

**6 pièges** rencontrés et corrigés pendant l'implémentation :
1. Le titre archi en bio ne suffit pas (cf. motif 1) — sans juge, deux faux `studio_actif` du CSV
   seraient sortis `compte_perso`/`hors_cible`.
2. Feed volumineux et pro ≠ actif (`helene.gombert` : 447 posts, 17k abonnés, email pro, mais feed
   incohérent) → `studio_dormant`, pas `studio_actif`.
3. Hashtags composés **contigus** (`#architectedinterieur`) non captés par un filtre à espace
   (`architecte dinterieur`) — corrigé en Task 2 (Notes de revue) : la découverte retient tout post
   dont les `hashtags` intersectent `ARCHI_HASHTAGS`, formes contiguës ajoutées à
   `PRESCRIBER_KEYWORDS`.
4. Cache `HandleVerdict` partagé CHR/archi : un même handle peut être vu par les deux funnels
   (notamment via `#agencement`, retail/CHR-adjacent) → collision de verdict possible. Corrigé
   dans `run_prescripteurs` : clé de cache **préfixée `arch:`** (`f"arch:{handle}"`), n'affecte pas
   `run_instagram` (CHR bit-à-bit intact).
5. Matcher SIRET CHR-gated appelé « tel quel » aurait fait une recherche Sirene HTTP (voire un
   arbitre LLM) par candidat pour un résultat structurellement `None` (NAF archi jamais accepté par
   `classify_naf`) — coût réseau réel sur un flux VOLUME MAX. Corrigé : `run_prescripteurs` et
   `classify_prescripteurs` appellent `match_fn=None`, aucune perte fonctionnelle
   (`establishment_name` retombe sur le nom découvert).
6. `_city_from_location` partagée CHR/archi cassait les communes IdF à tiret (« Boulogne-Billancourt »
   tronqué en « Boulogne ») — corrigé en découpant uniquement sur `,` et le tiret **espacé**
   (`' - '`), jamais le tiret collé des noms composés (dette CHR documentée, réutilisée par l'archi).
7. **(T6, run réel)** Deux comptes du run borné classés `studio_actif` (l'un même T2, avec
   `hospitality_proof=true` sur un run) alors qu'ouvrir leurs posts au navigateur révèle un
   hors_cible évident : `jks_ebenistes` (bio « Ébénisterie | Atelier | Mobilier | Agencements |
   Sur-mesure » en **lettres stylisées Unicode** — la garde `_norm` ne faisait qu'un NFD (retrait
   d'accents), jamais de NFKC, donc les lettres mathématiques italiques ne se réduisaient jamais à
   `ebenisterie` et le garde artisan ne matchait pas) et `schmidt_cambrai` (franchise « 1er
   fabricant français », mot absent de `_ARTISAN_KW`). Corrigé : `prescriber_guards._norm` applique
   NFKC avant NFD (même correctif que `siret_matcher.clean_name`) et `_ARTISAN_KW` inclut
   `fabricant`. Les deux comptes sont désormais écartés au garde (gratuit, déterministe, 0 appel
   LLM) — élimine la non-déterminisme LLM observée sur `schmidt_cambrai` (T2 sur un run, T3 sur un
   autre, faute de garde). `mokko_agencement` (atelier de fabrication sur mesure, bio sans mot-clé
   artisan exact, capté seulement par une légende de post « MENUISIER-FABRICANT ») reste un faux
   positif LLM accepté (toujours T3, jamais T1/T2 sur 3 runs répétés → aucune violation du gate
   dur ; dans la marge de précision ≥ 70 % tolérée).

## 3. Architecture

Flux **parallèle** à `run_instagram`, réutilisant l'infrastructure existante :

```
scrape_hashtags(ARCHI_HASHTAGS)
  -> discover_prescripteurs()            [nouveau, Task 2 — aucun filtre CHR/IdF]
  -> verdict_cache.should_rejudge("arch:<handle>")   [cache préfixé archi]
  -> scrape_profiles()                   [réutilisé tel quel]
  -> classify_prescripteurs()            [nouveau, Task 3 — gardes + juge + tiering]
       gardes déterministes (hors_cible sûr) -> juge LLM (studio_actif/dormant/compte_perso)
       -> tiering (T1: studio tagué sur chantier CHR détecté ; T2: preuve hospitality ; T3: générique)
  -> verdict_cache.upsert("arch:<handle>", ...)
  -> PRESCRIBER_ROUTING[label] -> LeadCandidate(population='architecte', ...)  [Task 4]
  -> _process_candidate (contournement is_architecte, Task 1) -> Opportunity
```

**Réutilisé sans modification fonctionnelle** : `scrape_hashtags`, `scrape_profiles`,
`verdict_cache` (module), `_match_result`/matcher SIRET (appelé nulle part côté archi —
`match_fn=None`), `_process_candidate` (branche additive), `compute_score` (familles
`PRESCRIBER_HOT/WARM` additives, jamais émises par un lead CHR).

**Nouveau, spécifique archi** : `ARCHI_HASHTAGS`, `PRESCRIBER_KEYWORDS`, `discover_prescripteurs`,
les gardes prescripteurs, `judge_prescripteur` (prompt `_PRESCRIBER_SYSTEM` dédié),
`classify_prescripteurs`, `_build_tagged_studios` (T1), `PRESCRIBER_ROUTING`, `run_prescripteurs`.

## 4. Espace de labels, tiering, routage

Labels prescripteurs (fixés) : `studio_actif | studio_dormant | compte_perso | hors_cible` (+
`noise` en pratique, jamais émis en gate mais géré par le routage par cohérence avec le CHR).

Mapping label → lead : `studio_actif`/`studio_dormant` → **lead** ; `compte_perso`/`hors_cible`/
`noise` → **verdict caché, pas de lead**.

Tiers de priorité (`studio_actif` uniquement) :
- **T1** — studio tagué sur un chantier CHR détecté par la machine (accroche « j'ai vu votre
  projet X ») → `secondary_signals += ['projet CHR détecté']`.
- **T2** — `studio_actif` avec preuve hospitality/CHR dans son portfolio →
  `secondary_signals += ['portfolio hospitality/CHR']`.
- **T3** — `studio_actif` générique, aucun libellé de tier additionnel.

`main_signal` = `'prescripteur actif'` pour tous les leads archis (label neutre, membre d'aucune
famille de scoring CHR → aucun bonus de nature, score naturellement bas, cohérent avec l'objectif
VOLUME). Les libellés de tier portent la priorité : `projet CHR détecté` = +3, `portfolio
hospitality/CHR` = +2 (familles NOUVELLES, jamais émises par un lead CHR → scores CHR inchangés).
`lifecycle_label` réutilise la colonne existante (`studio_actif`/`studio_dormant`), pas de nouvelle
colonne au-delà de `population`.

## 5. Gates d'éval

- **Offline (pytest, aucun réseau/LLM)** : gardes déterministes, routage, scoring additif, non-
  régression CHR — couverts par `backend/tests/`.
- **LLM live, gate d'acceptation** (`python -m app.ingestion.eval.prescripteurs_run`, nécessite
  `OPENAI_API_KEY`) : précision `studio_actif` ≥ 70 %, 0 `hors_cible` classé en tier T1/T2.
  Run initial (2026-07-10, 15 comptes sonde) : **82 % de précision** (9/11), 0 violation →
  `GATES ... OK`.
- **Run réel borné** (`python -m app.ingestion.run --mode prescripteurs --limit 20`, coût
  Apify+LLM assumé) : 74 posts récupérés, 46 leads `population='architecte'` créés
  (`main_signal='prescripteur actif'`, `lifecycle_label` 45×`studio_actif`/1×`studio_dormant`,
  tiers T2 (15)/T3 (31) constatés), confirmé par requête base directe.
- **Passe d'annotation navigateur** (exécutée après le run réel, T6) : 5 comptes du run ouverts au
  navigateur (posts + bio, invisibles depuis la seule grille anonyme du scrape) et annotés dans
  `architectes_groundtruth.csv` (`provenance=annotation_browser`, snapshots figés dans
  `snapshots_architectes/` via un appel `scrape_profiles` réel) : `mathildecros.archi` et
  `cabinet_pauline.s` confirment `studio_actif` (contenu de projet réel malgré un email `@gmail`
  pour la première, neutralisé par la cadence quasi quotidienne et le vocabulaire technique de
  chantier) ; `jks_ebenistes`, `mokko_agencement`, `schmidt_cambrai` sont en réalité `hors_cible`
  (artisan/fabricant, cf. piège #7 ci-dessus) alors que le run live les avait classés
  `studio_actif` (et `schmidt_cambrai` ponctuellement en T2). Ce dernier point est une **violation
  réelle et reproduite du gate dur** (« 0 hors_cible en T1/T2 »), corrigée par un renforcement des
  gardes déterministes (piège #7 : NFKC + mot-clé `fabricant`), pas par un relâchement du seuil.
  Gate ré-exécuté sur l'échantillon élargi (20 comptes) après correctif : **stable sur 3 runs
  consécutifs**, précision 79 % (11/14), 0 violation T1/T2 → `GATES ... OK`.
- **Non-régression CHR (obligatoire à chaque tâche et au gate final)** :
  `python -m app.ingestion.eval.match_eval` (0 faux merge, rappel ≥ plancher — 8/9 de référence,
  8/17 constaté au 2026-07-10 suite à l'élargissement du jeu de fixtures, gate vert) et
  `python -m app.ingestion.eval.run` (rappel opening = 100 %, précision segment chaud ≥ 60 %,
  82 % constaté au 2026-07-10) — les deux verts, aucune régression (le correctif de gardes ne
  touche que `prescriber_guards.py`, module dédié A1, jamais importé par le funnel CHR).

## 6. Hors périmètre A1 (à ne PAS implémenter dans cette brique)

- **Annuaires** (CFAI, Houzz…) comme source de découverte → brique **A2**.
- **Watchlist « nouveau projet »** (re-visite périodique des studios pour détecter un post de
  nouveau chantier, booster T1 dynamique) → brique **A3** (réutilisera le cache `HandleVerdict`).
- **Enrichissement SIREN des architectes** (élargir le NAF-gate du matcher au-delà du CHR :
  71.11Z/74.10Z) → **A2**. En A1 le matcher reste CHR-gated (no-op propre pour les archis,
  `match_fn=None`) et le juge travaille sur le profil seul.
- **Événementiel** (2e canal de vente historique d'Alexis) → hors A1 (focus pur archi
  d'intérieur).
- **Génération de messages** spécifiques prescripteurs (accroche « j'ai vu votre projet X ») →
  réutilise la génération existante, non spécialisée en A1.
- **Filtre géographique IdF** pour les archis → volontairement ABSENT (VOLUME MAX national,
  décision produit).
