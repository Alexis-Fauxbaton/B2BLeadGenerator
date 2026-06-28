# Plan A — Fiches de qualité + contact fiable (deux niveaux)

Date : 2026-06-28

## Objectif (nord)
**Beaucoup de fiches de qualité, avec un contact fiable.** Deux axes :
- **Volume** : vrais locaux CHR frais, bien filtrés (vient surtout des *sources* — hors scope de ce spec).
- **Qualité + contact** : chaque fiche est un vrai local dans sa fenêtre d'achat, avec un contact qu'on affiche **seulement si on est sûr** (précision d'abord).

Choix structurant validé : **A — l'opportunité = le local** (l'établissement qui ouvre/est repris). Le **décideur est un attribut** (un moyen de joindre), pas une fiche à part. On ne modélise PAS l'opérateur comme entité qui regroupe des locaux (= option B, hors scope).

## Décisions verrouillées
1. **Contact à deux niveaux** : `établissement` (ligne publique du lieu, forte couverture) vs `décideur` (la personne identifiée). Distinction au niveau du **contact**, pas de l'opportunité.
2. **Modèle de données plat, deux blocs** — PAS de table `Contact` (YAGNI tant qu'on est à ~1 valeur par canal). On migrera vers une table `Contact` le jour où il faudra *plusieurs* contacts du même canal.
3. **Précision d'abord** : on n'affiche un contact que si la confiance est haute, sinon « à trouver ».
4. **création/reprise** se décide sur le registre (`origineFonds`), pas sur un proxy (avis Places).

## 1. Modèle de données (colonnes plates, deux blocs)

**Bloc établissement** (là où écrivent Places / OSM / scrape — ce SONT des contacts établissement) :
- existants : `phone`, `email`, `website`, `instagram`, `facebook`, `latitude`, `longitude`, `review_count`
- **nouveau** : `contact_confidence` (TEXT : `"haute" | "moyenne" | "basse"` ou NULL)

**Bloc décideur** (la personne) :
- existant : `decision_maker` (nom, depuis BODACC/Sirene)
- **nouveaux** : `decision_maker_email` (TEXT), `decision_maker_confidence` (TEXT)

→ 3 colonnes ajoutées, migration légère (`_run_lightweight_migrations`), exposées dans les schémas Pydantic + types front.

## 2. ETL — distinguer établissement vs décideur (source + valeur)

La **source** est le backbone (~90 % des cas) ; une **heuristique sur la valeur** tranche les 2 cas ambigus.

| Source | Canal | Niveau |
|---|---|---|
| Google Places / OSM | tél, site | établissement |
| Scrape site | Instagram/Facebook | établissement (compte enseigne) |
| Scrape site | **email** | role-based (`contact@/resa@/info@/hello@`) → **établissement** ; nominatif (`marie@`, `marie.dupont@`) → **décideur** (`decision_maker_email`) |
| Sirene/BODACC | nom dirigeant | décideur (identité) |
| Génération pattern `prénom.nom@domaine` + SMTP | email | décideur (`decision_maker_email`) |

Note : l'Instagram de l'enseigne reste niveau **établissement** même si, pour un petit CHR tenu par son patron, il rabat souvent sur le décideur (couverture/volume). Le niveau **décideur** est réservé aux canaux où on a **identifié la personne**.

## 3. Scoring de confiance + règles d'affichage (précision d'abord)

Pattern validé (Tamr / régression logistique relationnelle) : score, seuil haut → afficher, sinon « à trouver ».

**Contact établissement** — `contact_confidence` :
- **haute** : match Places **géo-confirmé** (lieu ≤ 200 m du point Sirene).
- **moyenne** : concordance du **nom d'enseigne** (tokens distinctifs, nom **non-holding**) + ville cohérente.
- **basse / NULL** : sinon.
- **Affichage** : on montre le contact établissement si confiance **≥ moyenne** ; sinon « à trouver ».

**Contact décideur** — `decision_maker_confidence` :
- **haute** : nom dirigeant connu **+** email (scrapé nominatif **ou** pattern avec taux de match ≥ 0,8) **+** vérif SMTP/MX OK. Bonus si le nom du dirigeant est corroboré sur le site/compte.
- **basse** : nom générique / **holding** → jamais auto-attribué.
- **Affichage** : on montre le contact décideur **seulement si haute** ; sinon « à trouver ».

RGPD : prospection B2B sous **intérêt légitime** + opt-out ; **pas de mobile perso** ; le nom du dirigeant est une donnée perso (même pro) → traçabilité/suppression.

## 4. création vs reprise — via `origineFonds` (remplace le misfire)

BODACC expose `listeetablissements.etablissement.origineFonds` :
- `"Création d'un fonds de commerce"` → **création récente**.
- contient `achat` / `précédent` / `reprise` → **reprise**.

Action :
- **Parser `origineFonds`** dans `bodacc.py` ; en faire **la** source création/reprise (renforce la règle (a) "précédent exploitant → reprise").
- **Supprimer la reclassification (b)** par les avis Places (`REPRISE_REVIEW_THRESHOLD`) : elle a produit un faux (Lapérouse Holding, requalifiée à tort en reprise via les 2333 avis du Café Lapérouse mal attribués). `review_count` **reste** uniquement un **qualifieur de score** (≤20 +1, ≥200 −1).

création et reprise sont **toutes deux de bons leads** (un local à équiper) — c'est la **nature/timing/message** qui change, pas la qualité. Ne pas pénaliser la création.

## 5. Garde holding (flag, pas drop)

Une société mère passe le filtre NAF (ex. 55.10Z) mais n'est **pas un local à équiper**, et son contact « déborde » sur le vaisseau amiral du groupe.

Marqueurs : nom (`holding`, `groupe`, `invest`, `participations`, `financière`, `food retail`), NAF 64.20Z quand présent, objet social générique (« création, acquisition, prise à bail… de tout hôtel, restaurant ou établissement de même nature »), structure (président = une société).

Action : **flaguer** « groupe/holding — à vérifier », **ne pas auto-attribuer** de contact (confiance forcée basse), **mais ne pas droper** (la holding peut fronter un vrai local ; on remonte au dirigeant via les dirigeants déclarés au RCS — Pappers/INPI ouvert —, **pas** via les bénéficiaires effectifs, API restreinte depuis le 31/07/2024).

## 6. Déjà livré (référence)
- **Validation Places par distance** : la proximité **confirme** mais ne **veto** jamais (siège souvent à plusieurs km du local). `_match_ok`, testé.
- **Bonus « signaux croisés » par FAMILLE** (`scoring._signal_families`) : reprise + changement propriétaire = 1 famille → plus de +1 indu.
- **Qualifieur fraîcheur** `review_count` (±1) + colonne + re-score dans la passe contact.

## 7. Hors scope (V2 / autres specs)
- Table `Contact` normalisée (si besoin de plusieurs contacts/canal).
- **Opérateur comme entité** (option B : regrouper les locaux d'un même opérateur).
- **Recherche active du canal décideur** (compte Insta principal de l'enseigne / dirigeant) via search-API ou Apify — c'est le **pont local→opérateur que personne ne résout sur étagère** ; gros différenciant, mais Phase 2 (payant).
- Acquisition Instagram-first ; élargissement des sources (France Travail, géo).

## Tests à prévoir
- Routage email role-based vs nominatif (établissement vs `decision_maker_email`).
- `contact_confidence` : géo-confirmé → haute ; nom+ville non-holding → moyenne ; sinon basse.
- `decision_maker_confidence` : pattern ≥0,8 + SMTP → haute ; holding → basse.
- `origineFonds` : « Création d'un fonds de commerce » → création ; « Achat… » → reprise.
- Garde holding : nom/objet holding → flag + contact non auto-attribué.
- Non-régression : suppression de la reclassification (b) (un lead « création » à fort `review_count` reste « création récente », pas « reprise »).

## Migration
3 colonnes via `_run_lightweight_migrations` : `contact_confidence`, `decision_maker_email`, `decision_maker_confidence`. Rétro-compatible (NULL par défaut). La passe contact repeuple en `--mode contact` (reset pour re-mesurer).
