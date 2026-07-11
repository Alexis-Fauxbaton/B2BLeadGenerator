# Lancement volume max — prévu le 2026-07-12 (rien ne tourne avant)

Décision du 2026-07-11 : planifié aujourd'hui, **lancé demain uniquement**.

## Objectif
Passer de ~44 studios (Insta A1) + ~850 attendus (annuaires A2) à **plusieurs
milliers de leads architectes d'intérieur qualifiables**, avec téléphone en
première classe (closers) et la règle vide > faux partout.

## Les deux gisements retenus
1. **Stock Sirene complet** — toutes les unités ACTIVES NAF 74.10Z + 71.11Z
   (dizaines de milliers, gratuit, SIREN/dirigeants natifs). Chalut, pas ligne :
   filtre de qualification mots-clés au rendement mesuré + gardes négatives
   (design graphique/produit, paysagistes, architectes bâtiment), [ND] écartés.
2. **Balayage Google Places par ville** — Text Search « architecte d'intérieur
   \<ville\> » sur les villes ordonnées par population, Place Details champs
   Contact uniquement → **téléphone + site natifs**. Budget dur en € par run,
   curseur de reprise pour rester dans le crédit mensuel gratuit.

Dédup inter-sources : soft-merge A2 étendu (nom+ville + corroboration
obligatoire : domaine du site / CP / dirigeant / téléphone identique) ; un
recouvrement ENRICHIT la fiche survivante, jamais de doublon. Gate 0 faux merge.

## Séquence de demain (dans l'ordre)
1. Workflow de plan (sondes réelles bornées → plan TDD → revue adverse qui
   exécute → fixeur). Sondes : volumétrie/pagination réelles du stock (le
   plafond de pagination de recherche-entreprises est LE point à prouver),
   rendement Places sur 3 villes (~10 Details/ville max), recouvrement
   inter-sources sur 10 studios UFDI.
2. Exécution du plan (connecteurs + tests sans réseau + gates de
   non-régression : match_eval 8/9, prescripteurs_run OK, éval CHR intacte).
3. Tir : annuaires A2 → stock Sirene par tranches → Places top-N villes dans
   le gratuit, points de contrôle sqlite chiffrés entre chaque étape.

## Reprise technique (pour l'orchestrateur)
- Script du workflow de plan déjà écrit et sauvegardé :
  `~/.claude/projects/.../workflows/scripts/plan-volume-max-wf_24b86ba6-da2.js`
  (runId `wf_24b86ba6-da2`, stoppé avant toute sonde payante — relancer avec
  `scriptPath` + `resumeFromRunId`).
- Prérequis : A2 exécuté et mergé (le plan volume s'aligne sur ses patterns
  de connecteur et son soft-merge).

## Pour lancer demain
Dire simplement : **« Lance le volume »** — tout part de ce brief.
