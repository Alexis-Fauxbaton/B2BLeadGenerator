# Design — Enrichissement contact (gratuit, Approche A)

Date : 2026-06-28

## Objectif
Récupérer **automatiquement** le contact des leads (priorité : **email, téléphone,
Instagram** — entreprise ou décideur), avec des sources **gratuites**, et
**instrumenter la couverture** pour chiffrer ce que le gratuit rapporte vraiment
(→ décider ensuite si on ajoute une search API ou Google Places).

Faisabilité mesurée :
- OSM (Paris centre, meilleur cas) : tel 48%, site 37%, Insta 7%, email 9%.
- Scrape de site : fonctionne (emails + Instagram récupérés sur de vrais sites CHR).
- **Maillon faible = découverte du site** pour les créations fraîches.

## Sources (waterfall, par lead)
1. **Géoloc Sirene** (déjà gratuite via recherche-entreprises) → coords pour OSM + liens Maps.
2. **OSM / Overpass** (sans clé) autour des coords, match par nom → `phone`,
   `website`, `contact:instagram`, `contact:email`.
3. **Scrape du site** (si une URL est trouvée) : home + `/contact` +
   `/mentions-legales` → email, instagram, facebook, téléphone (le pilier pour
   email/Insta, légalement présents dans les mentions légales).

Pas de search API ni Google Places à ce stade (décision : mesurer d'abord).

## Composants — `app/ingestion/enrichment/`
- `osm.py` — `lookup_osm(name, lat, lon, radius)` → dict de contacts. Fail-soft, UA, délai.
- `website_scraper.py` — `scrape_contacts(url, max_pages=3)` → dict. Filtrage des
  faux emails (`@sentry`, `.png`, `example`…), handles Insta/FB hors `p/reel/explore`.
- `contact_enricher.py` — orchestre le waterfall pour un lead ; ne remplit que les
  champs vides ; renvoie chaque valeur + sa source.

## Modèle (migration légère)
Ajout à `Opportunity` : `phone`, `email`, `website`, `instagram`, `facebook`,
`latitude`, `longitude`, `contact_enriched_at`. Géoloc capturée par `apply_sirene_data`.

## Intégration — passe dédiée (comme `reenrich`)
Lente (OSM + scrape) → **pas inline**. Passe état-based :
- cible `contact_enriched_at IS NULL` ; pour les leads sans coords mais avec SIREN,
  on récupère les coords via Sirene à la volée.
- ne remplit que les champs vides (préserve les saisies manuelles) ; marque
  `contact_enriched_at` même si vide (évite de re-scanner sans fin).
- CLI `--mode contact` + endpoint `POST /api/dev/contact-enrich`.
- **Stats = le gap chiffré** : `{scanned, with_phone, with_email, with_website,
  with_instagram, none, errors}`.

## Frontend (après mesure)
Section "Contact" : champs éditables + valeurs cliquables (`tel:`/`mailto:`/site/Insta)
+ helpers (Maps, recherche) quand vide ; messages générés actionnables.

## Robustesse & légal
Fail-soft par lead (timeout/404/rate-limit → on saute). Overpass : délai + cache.
Scrape : max 3 pages, timeout court, taille limitée. B2B + mentions légales
publiques + OSM ODbL (stockable) → posture propre ; note GDPR (intérêt légitime + opt-out).

## Tests (offline)
Extraction email/insta/tel depuis HTML fixture ; parsing tags OSM depuis JSON
fixture ; filtrage des faux emails.
