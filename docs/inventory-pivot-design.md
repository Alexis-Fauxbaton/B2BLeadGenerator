# Design — Pivot « inventaire + étiquetage » (matching SIRET, délta Sirene, funnel Insta v2)

Date : 2026-07-05 · Statut : spec validée en discussion, en attente de relecture

## 1. Problème / constat

1. **Le funnel Insta décide au mauvais endroit.** Les portes amont (`discover`,
   `judge`) tranchent la fraîcheur sur une caption de post (preuve pauvre), alors
   que la vérité est sur le profil et dans le registre (preuve riche, exploitée
   trop tard et trop peu). Conséquences mesurées : MOKA (chaîne établie) passe,
   Chick'n Tikka (bruit) passe, tregusto/Giorgina indécidables.
2. **Instagram ne peut pas faire le volume.** Les hashtags ne voient qu'une
   fraction des ouvertures. Le registre Sirene les voit toutes (immatriculation
   obligatoire, 1-6 mois avant ouverture), gratuitement.
3. **Le merge Insta↔SIRET est faisable** (testé le 2026-07-04 sur les 20
   snapshots d'éval, API live) : ~65 % de matchs immédiats avec nom nettoyé +
   adresse géocodée, ~80 % attendus avec pivot Places + réconciliation différée.
   Un échec de merge ne coûte rien (le lead vit sans SIRET).

**Décision produit** : passer d'un funnel qui *filtre* (drop irréversible à
l'entrée) à un inventaire qui *étiquette* (tous les CHR en base, label de cycle
de vie réévaluable, les `opening_soon` bien détectés dedans).

## 2. Objectifs / non-objectifs

Objectifs : recall ~100 % sur les ouvertures via le registre ; qualité par
corroboration croisée (SIRET récent + preuve profil) ; 20-50 leads/jour qualité
« agent » en IdF pour quelques €/jour ; aucune régression sur l'éval existante.

Non-objectifs (pour l'instant) : vérification agentique navigateur (tier
optionnel ultérieur) ; moissonneur presse ; France entière (un filtre à changer,
pas une brique) ; France Travail (roadmap existante, inchangée).

## 3. Architecture — 4 briques, dans l'ordre

### Brique 1 — `enrichment/siret_matcher.py` (remplace `backfill_siren`)

Chaîne de matching, chaque étage ne traite que ce que le précédent n'a pas résolu :

1. **Nom nettoyé** (strip emojis/slogans/séparateurs `|•–`) + ville →
   `recherche-entreprises.api.gouv.fr/search` (`q=`, `code_postal`/`departement`
   si connus). L'index de l'API matche aussi les enseignes (cas CC ROQUETTE →
   CHÈRES COUSINES).
2. **Adresse** (`businessAddress` structurée, sinon regex sur bio + captions) →
   géocodage BAN (`api-adresse.data.gouv.fr`) → `/near_point` (rayon ~0,1 km,
   `section_activite_principale=I`).
3. **Arbitre LLM** sur les 2-6 candidats géo/nom-filtrés : profil Insta résumé
   vs candidats (nom, enseignes, NAF, adresse, date_creation) → `match|no_match`
   + confiance. **Jamais de merge nom-seul sans arbitre** (piège Auréa : un
   « AUREA » 56.10A existe ; la bio « bijoux, Portugal » doit le rejeter).
   Une adresse à ±80 m + NAF CHR + récence est quasi décisive seule (cas
   Tre Gusto → OCOIN).

Sortie : `{siren, siret, confidence, method}` ; stockée sur l'Opportunity avec
la méthode de match (traçabilité/debug).

### Brique 2 — Connecteur délta-Sirene (volume)

Nouveau connecteur (même interface que `BodaccConnector`) : établissements
(SIRET, pas seulement SIREN — les chaînes ouvrent des SIRET) créés récemment,
NAF 55/56, départements configurables (IdF d'abord). Upsert `source="sirene"`,
`main_signal="ouverture prochaine"`, dédup par SIRET. Corroboration : si un
lead Insta matche un SIRET délta → fusion (l'entité garde les deux provenances,
score ++).

### Brique 3 — Funnel Insta v2 (fin des drops sur caption)

- `discover` : inchangé dans son rôle (écrémage déterministe CHR+géo,
  recall-only). Le juge caption **disparaît**.
- `scrape_profiles` sur tous les survivants de discover (coût ~0,2-0,3 ct/profil,
  amorti par le cache de verdicts).
- Garde-fous déterministes profil étendus (gratuits, avant tout LLM) :
  `postsCount > 150` et `_profile_long_history` (existants) + horaires en bio
  (regex `\d{1,2}h`, « ouvert du », « open everyday »…) + lien résa (zenchef,
  thefork, sevenrooms, opentable, resy) + ≥ 2 adresses en bio (chaîne).
- **Un seul juge LLM par compte** (évolution de `_judge_profile`, appel
  unitaire) sur le dossier complet : bio, compteurs, catégorie, 6-12 derniers
  posts datés, caption d'origine, **résultat du matching SIRET (date_creation)**.
  Sortie : label de cycle de vie `opening_soon | just_opened | established |
  chain_multisite | not_venue | noise | unknown` + confiance + extraction
  adresses/emails (existant). Le label remplace l'admission binaire ; en base,
  il pilote `main_signal` et le score, plus aucun drop définitif côté Insta.

**Cache de verdicts** — table `handle_verdicts {handle, verdict, confidence,
judged_at, revisit_after, profile_hash}`. Un handle revu par les hashtags n'est
re-scrapé/re-jugé que si `now > revisit_after` :

| Verdict | revisit_after |
|---|---|
| not_venue | +12 mois |
| established / chain_multisite | +6 mois |
| noise / unknown | +2 mois |
| opening_soon / just_opened | pas de cache — watchlist active (hebdo) |

Un événement registre sur l'entité liée (BODACC : reprise, changement de
propriétaire) déclenche un re-jugement immédiat, hors fenêtre.

### Brique 4 — Watchlist + réconciliation (s'appuie sur `refresh`/`reenrich`)

- **Watchlist** : les `opening_soon`/`just_opened` (avec ou sans SIRET) sont
  re-scrapés chaque semaine → rafraîchit le label (travaux → ouverture imminente
  = moment de relance) et retente le matching.
- **Réconciliation** : toute Opportunity Insta sans SIREN repasse par le
  matcher chaque semaine. Le temps travaille pour nous : l'adresse arrive en
  bio, le SIRET est créé (cas Brasserie de la Fontaine), la fiche Places naît.
  Upgrade automatique en lead corroboré (score ++, adresse, dirigeant).

## 4. Stratégie de test (exigence : extensif)

- **TDD par brique** ; fonctions pures partout où possible (comme `discover`).
- **Matcher (brique 1)** : le harnais d'éval existant s'étend — colonne
  `expected_siren` dans `instagram_groundtruth.csv` (renseignée depuis le test
  du 2026-07-04) ; l'éval mesure précision/recall du matching sur les 20
  snapshots **sans réseau** (réponses API figées en fixtures JSON) + un mode
  live optionnel (marqué lent) qui rejoue contre les vraies API. Cas de
  non-régression obligatoires : Tre Gusto (adresse seule), Chères Cousines
  (enseigne), Auréa (rejet arbitre), Lourmarin (no-match propre).
- **Juge v2 (brique 3)** : éval snapshots existante = gate de non-régression ;
  chaque label du groundtruth doit être reproduit ou amélioré ; les garde-fous
  déterministes ont leurs tests unitaires (horaires, résa, multi-adresses) ;
  MOKA et Chick'n Tikka doivent tomber au bon étage (déterministe pour MOKA,
  corroboration pour Chick'n Tikka).
- **Délta Sirene (brique 2)** : test d'intégration live borné (1 jour, 1 dept) +
  fixtures pour la logique de fenêtre/dédup.
- **Cache/watchlist (briques 3-4)** : tests unitaires des fenêtres de revisite
  (horloge injectée) ; scénario bout-en-bout : handle jugé noise → revu à +3
  mois → requalifié opening après création SIRET.
- **Mesure avant/après** sur l'éval complète (precision/recall par label) à
  chaque brique ; publication du diff dans le PR.

## 5. Risques & parades

- **Fenêtre `/near_point` trop étroite** (Mer Paulette : 16 vs 20 rue des
  Bains) → rayon paramétrable, arbitre tolérant au ±quelques numéros.
- **Faux merges** → arbitre obligatoire hors match adresse exacte ; méthode de
  match stockée ; `reenrich` supprime déjà les faux positifs confirmés.
- **Rate limits** (recherche-entreprises 7 req/s, BAN) → cache + throttle,
  pattern déjà en place dans les enrichisseurs.
- **Champ enseigne vide à l'immatriculation** (cas MOKA introuvable) → pivot
  Places en étage 2bis si besoin (différé tant que le taux mesuré reste ≥ 60 %).

## 6. Ordre de livraison

1. Brique 1 (matcher) — upgrade immédiat des leads Insta existants.
2. Brique 2 (délta) — le volume.
3. Brique 3 (funnel v2 + cache) — la qualité MOKA/Chick'n Tikka.
4. Brique 4 (watchlist/réconciliation) — le tail et le timing de relance.
