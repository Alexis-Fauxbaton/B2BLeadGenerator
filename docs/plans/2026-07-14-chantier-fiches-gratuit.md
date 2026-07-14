# Chantier « plus de fiches » — 100 % gratuit (préparé le 2026-07-14, PAS lancé)

Décisions d'Alexis : budget **0 €**, population **intérieur pur** (pas d'élargissement
aux architectes bâtiment de l'Ordre), périmètre **France + Monaco**.
Doctrine inchangée : VIDE > FAUX, gates durs jamais affaiblis, un seul écrivain
SQLite à la fois, throttles polis (2,1 s Insee / 2,5 s scraping), pas de
contournement anti-bot (Houzz, Pages Jaunes/DataDome exclus).

## Le constat qui structure le chantier

- `sirene_stock` : 2 987 fiches en base, **0 téléphone** (SIREN/dirigeant natifs
  mais aucun site connu → jamais enrichies).
- Le tir stock a gardé 0,7 % de ~450 000 unités actives ; le filtre v1 en
  qualifiait ~9 % → **réservoir d'environ 25 000 candidats ambigus** rejetés par
  prudence, re-vérifiables gratuitement.
- Le pipeline téléphone vient d'être durci (fixes du 2026-07-14 : cap 5 Mo,
  Monaco +377, convention +33 (0)X, gardes anti-template statique + dynamique,
  variantes d'URL) → tout ce que ce chantier découvre en profite.

## Brique A — Moteur de découverte de site (le socle, à construire en premier)

Entrée : nom d'établissement (+ ville, dirigeant, SIREN). Sortie : URL du site
PROPRE du lead, ou rien.

- Recherche web gratuite et polie (DuckDuckGo HTML / Bing sans clé, throttle
  ≥ 2,5 s, cache de requêtes préfixé `sitefind:` dans le cache verdicts).
- **Verrou d'identité avant d'accepter un site** (VIDE > FAUX) : le site doit
  corroborer ≥ 1 signal fort — nom quasi-exact dans title/h1, OU ville/CP de la
  fiche, OU nom du dirigeant, OU SIREN/SIRET en mentions légales. Sinon : vide.
- Réutilise `_own_site` (exclusion plateformes) et le scraper durci.
- Testable sans réseau (fixtures HTML), gate : échantillon GT N=50 fiches
  stock annotées à la main — **0 site attribué à tort** (l'homonyme même CP de
  la fixture adverse doit rester vide), rendement mesuré (attendu ~40-60 %).

## Brique B — Téléphones pour le stock existant (premier rendement, en jours)

Appliquer A aux 2 987 `sirene_stock` (+ 7 `jeunes_studios`) : site trouvé →
`enrich_phones` + `enrich_site_contacts` existants.

- Écriture du site dans `website` UNIQUEMENT si verrou A passé.
- Projection honnête : ~50 % de sites trouvés × ~60 % affichant un tél
  ≈ **+800 à 1 000 téléphones gratuits**, + emails/Insta au passage.
- Tourne en fond (~2-3 jours au throttle poli), commit par fiche, reprenable.

## Brique C — Repêchage des ~25k ambigus Sirene (le gros volume, plus long)

1. Re-balayage du stock Insee (curseur `fetch_stock_etablissements` existant,
   gratuit, ~13 h) avec le filtre v1 LARGE → candidats « ambigus » stockés dans
   une table/CSV de travail (`stock_ambigus`), PAS dans opportunities.
2. Pour chaque ambigu : Brique A (site) → marqueurs « intérieur » sur le site
   (mêmes gardes négatives que `qualifies()` v2 : graphisme, paysage, bâtiment
   pur) → seuls les CONFIRMÉS entrent en base (`source='sirene_stock'`,
   dédup/corroboration forte existante inchangée).
3. Gates : échantillonneur GT existant (`eval/stock_gt_sample.py`) sur N=100
   confirmés — précision ≥ celle du tir (98,6 %), 0 hors-cible en tiers ;
   `run_cross_source_gate` 0 faux merge.
- Projection : si 15-25 % des ambigus se confirment → **+4 000 à 6 000 fiches**
  intérieur pur, avec site (donc enrichissables en tél).

## Brique D — Sonde annuaires intérieur gratuits (opportuniste, parallèle)

Inventaire 30 min (read-only) puis connecteurs pattern CFAI/UFDI pour les
accessibles : Archidvisor, SAD (Société des Architectes Décorateurs), listes
régionales CFAI non couvertes, fédérations déco. Exclus d'office : Houzz,
Pages Jaunes (anti-bot dur). Chaque annuaire = volume modeste (50-500) mais
certifié et souvent avec contact direct.

## Ordre d'exécution recommandé

1. **A + B** (le moteur + rendement immédiat sur l'existant) ;
2. **D** en parallèle de B (sonde légère) ;
3. **C** ensuite (long, s'appuie sur A rodé par B et ses gates).

Exécution : workflows/agents opus-sonnet (jamais fable en sous-agent),
plan TDD + revue adverse comme pour le volume max. Un seul écrivain SQLite :
B, C et toute passe d'enrichissement ne tournent jamais en même temps.

## Pour lancer

Dire « **Lance le chantier fiches** » — tout part de ce brief.
