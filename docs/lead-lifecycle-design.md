# Cycle de vie des fiches — Entité persistante vs Opportunité éphémère

Date : 2026-06-29
Statut : **design** (issu d'un brainstorm ; rien d'implémenté). Les blocs sont
annotés **[DÉJÀ]** (existe), **[À AJOUTER]** (à coder, faisable maintenant),
**[PHASE 2]** (bloqué sur un prérequis — Insta/scraping/scheduling).

## Objectif
Faire « vivre » les fiches dans le temps : une fiche naît d'un signal, avance
dans le cycle de vie du lieu, et **périme**. Il faut acquérir du neuf en continu
et rafraîchir l'ancien, sans état fragile à maintenir.

## 1. Le modèle conceptuel (le cœur)
Trois niveaux distincts — aujourd'hui **confondus** dans le modèle `Opportunity`
(qui mélange entité + opportunité + contact). La cible les sépare :

- **Entité** = le **lieu / l'affaire réel·le**. **Persistante** (vit jusqu'à
  fermeture). C'est *elle* qu'on **rafraîchit**.
- **Signal** = un **événement horodaté immuable** (BODACC création, opening-soon
  Insta, nb d'avis Places, « vérifié le X »…). On n'invalide jamais un signal :
  un refresh **émet un nouvel événement** qui *dépasse* les anciens. **[DÉJÀ]**
  table `Signal` existe (sous-exploitée).
- **Opportunité** (« la fiche ») = **projection éphémère** : une **fenêtre
  d'achat** sur l'entité, née d'un cluster de signaux. Elle ne se rafraîchit pas,
  elle **dérive** de l'entité + ses signaux, **transite**, puis **périme**. Une
  même entité peut porter **plusieurs** opportunités dans le temps (reprise 3 ans
  après, expansion…).

> Pattern CRM classique **Account (entité) vs Deal (opportunité)**.

## 2. Identité de l'entité (multi-clés)
Une entité n'est PAS « un SIREN » ni « un compte » : c'est le **lieu**, identifié
par un **jeu de clés optionnelles** :
- `siren` (clé **canonique** *quand connue*),
- `insta_handle`, `place_id` Google, `domaine` du site, `nom + ville + géo`.

**Règle de fusion / dédup** (résolution d'entité, façon "golden record") :
`SIREN si connu → sinon handle → sinon nom+ville+géo`.

Cas concrets :
- **Insta-first** : entité keyée par le **handle** (SIREN inconnu) — entité
  valide. Quand le SIREN apparaît (backfill), on l'**ajoute** à la même entité
  (réconciliation). Deux entités qui s'avèrent le même lieu → **fusion**.
- **BODACC-first** : entité keyée par le SIREN dès le départ.

**[À AJOUTER]** Aujourd'hui l'`Opportunity` a `siren`, `instagram`, `latitude/…`
mais pas de notion d'entité séparée ni de merge. Cible : une table/couche
**Entité** portant les identifiants + la merge-rule. *Décision ouverte* : vraie
table `Entity` séparée, ou d'abord une **clé d'entité dérivée** sur `Opportunity`
(plus léger, YAGNI) — à trancher au plan d'implémentation.

## Taxonomie des signaux (UN seul jeu, DEUX attributs)
On ne fait **pas** deux listes séparées « moment d'achat » vs « état » : ce sont
**deux propriétés du même événement**. Chaque signal porte :
- **poids d'achat** (0 si ce n'est pas un moment d'achat),
- **effet stage** (— si aucun).

Un signal peut avoir les deux, une seule, ou aucune (ex. heartbeat).

| Signal | Source | Poids d'achat | Effet stage | Statut source |
|---|---|---|---|---|
| création récente / opening-soon | BODACC / Insta | +3 | pré-ouverture | BODACC **[DÉJÀ]** · Insta **[PHASE 2]** |
| reprise | BODACC (`origineFonds`) | +3 | ouvert/repris | **[DÉJÀ]** |
| changement propriétaire | BODACC | (secondaire) | ouvert/repris | **[DÉJÀ]** |
| rénovation / travaux visibles | presse / observation | +2 | — (overlay chaud) | **[PHASE 2]** / manuel |
| recrutement | France Travail | +2 | — (overlay chaud) | **[À AJOUTER]** (roadmap 2) |
| expansion / nouveau point de vente | BODACC (étab. secondaire) | +2 | — | **[PARTIEL]** |
| **établi** | dérivé avis Places | 0 | établi → refroidit | **[DÉJÀ]** (data) |
| **fermé / radié** | Sirene `état=F` | 0 | fermé | **[DÉJÀ]** |
| **« vérifié le X »** (heartbeat) | passe refresh | 0 | — (juste un événement récent) | **[À AJOUTER]** |
| annonce presse / compte Insta trouvé | presse / scrape | 0 | — (provenance/preuve) | partiel |

Note : la **chaleur** (§4) = un signal à **poids d'achat > 0** encore **dans sa
fenêtre**, quel que soit le stage. Le **stage** vient des signaux à **effet stage**.
`review_count` **[DÉJÀ]** alimente le stage « établi » (fiable si match Places
géo-confirmé).

## 3. Cycle de vie (stage) — DÉRIVÉ
Fonction **pure** `lifecycle_stage(signaux, avis, dates, today)` — calculée à la
volée, **jamais stockée** (donc jamais désynchronisée) :
- **pré-ouverture** — opening-soon / création récente, ~0 avis.
- **ouvert récemment** — peu d'avis, création récente → *fenêtre d'achat ouverte*.
- **établi** — beaucoup d'avis / ancienneté → *fenêtre passée*.
- **fermé** — Sirene `état=F`, radiation BODACC.

**[À AJOUTER]** fonction + tests. Réutilise `review_count` **[DÉJÀ]**,
`detection_date` **[DÉJÀ]**, `main_signal` **[DÉJÀ]**.
⚠️ La fiabilité de `review_count` dépend d'un match Places géo-confirmé
(cf. `contact-tiering-design.md`) — sinon le stage « établi » peut être faux.

## 4. Chaleur, fraîcheur & péremption — DÉRIVÉES
**Point clé (validé) : le STAGE et la CHALEUR sont deux choses distinctes, elles
se COMBINENT, elles ne fusionnent pas.**
- **Stage** = descripteur de la vie du lieu (pré-ouverture / ouvert / établi /
  fermé). Cf. §3.
- **Chaleur** = un **moment d'achat ACTIF** : le dernier signal de type "moment
  d'achat" (cf. §Taxonomie) est encore **dans sa fenêtre** (récent). **Indépendant
  du stage** : un lieu *établi* peut être *chaud* (« établi mais chaud ») si un
  **recrutement / rénovation / reprise** vient de tomber — c'est de l'or (il
  réaménage). C'est précisément ce qui permet à une entité de porter **plusieurs
  opportunités dans le temps**.

Affichage combiné : « **établi · chaud (recrutement, J-5)** ».

**Péremption d'une opportunité** (affinée) :
- une opportunité **vit tant qu'un moment chaud est actif** (signal d'achat dans
  sa fenêtre) ;
- elle **périme** quand ce moment vieillit **hors fenêtre sans nouveau**, **ou**
  à la **fermeture** du lieu.
- → *établi sans moment récent* = froid/périmé ; *établi avec recrutement récent*
  = chaud ; *pré-ouverture* = chaud par nature.

`freshness(dernier_événement, today)` = temps depuis le **dernier** signal (tout
type) → `fraîche` → `à rafraîchir` → `périmée`. Les périmées ne sont pas
supprimées → **archivées** (historique gardé).
**[À AJOUTER]** fonctions `heat`/`freshness` + seuils (fenêtres par type de signal
à caler).

**[NEXT — date de fin explicite]** *Idée à garder pour plus tard* : quand une
source donne une **date explicite** (date d'ouverture annoncée en presse/Insta,
BODACC `acte.dateCommencementActivite` — ex. BEAR YTD « 12 juin 2026 », ou « à
dater du »), **borner l'opportunité avec cette date** plutôt qu'avec le staleness
dérivé (l'heuristique reste le *fallback*). Plus précis, mais pas prioritaire —
noté ici pour ne pas l'oublier.

## 5. Refresh = fan-out par canal (le "on rafraîchit l'entité")
Rafraîchir une entité = **fan-out sur ses clés connues**, re-lire chaque canal,
**réconcilier**, et **émettre de nouveaux signaux** (pas invalider les anciens) :

| Canal | Clé | Ce qu'on relit | Statut |
|---|---|---|---|
| Sirene | siren | état administratif, dirigeants, NAF | **[DÉJÀ]** (`reenrich`) |
| Google Places | nom/place_id + géo | avis (stage), statut, tél/site | **[DÉJÀ]** (`places.py`) |
| Site web | domaine | email/insta (mailto) | **[DÉJÀ]** (`website_scraper`) |
| **Instagram** | handle | compte ouvert ? nouveaux posts ? opening→ouvert | **[PHASE 2]** — *aucun connecteur Insta ; besoin Apify/scraping. Non fait car payant/fragile et pas encore validé.* |

**Cadence & coût** (le fan-out complet est le *concept*, pas chaque tick) : on
rafraîchit surtout les entités **actives** (en fenêtre) ; Sirene souvent
(gratuit), Places/Insta moins (payant). Les périmées → on lâche.
**[À AJOUTER]** généraliser `reenrich` en une passe `refresh` multi-canal qui
émet des `Signal` « vérifié le X ».

## 6. Les deux boucles (scheduling)
- **Acquérir (neuf)** : ingestion continue → crée/complète entités + émet signaux.
  - BODACC incremental **[DÉJÀ]** (`run_incremental`).
  - **Insta-first** (démarrer par les opening-soon) **[PHASE 2]** — *le connecteur
    Insta n'existe pas ; c'est le prochain leverage convenu (cf. roadmap).*
- **Rafraîchir (ancien)** : passe `refresh` périodique sur les entités actives
  **[À AJOUTER]** (base = `reenrich` **[DÉJÀ]**).
- **Ordonnancement** : cron/worker **[PHASE 2]** — *pas de scheduler ; aujourd'hui
  déclenché à la main / endpoint. Migration Postgres + hébergement = roadmap 3.*

## 7. UI — « la fiche vit »
- **Timeline** verticale des signaux (événements). **[À MOITIÉ LÀ]** — la fiche
  affiche déjà `signals`, à transformer en vraie chronologie.
- **Badge de stage** (pré-ouverture / ouvert / établi / fermé) + **jauge de
  fraîcheur** (« vu il y a 3 j » / « à rafraîchir » / « périmé »). **[À AJOUTER]**
- **Tri/filtre par stage + fraîcheur** → travailler les fiches *chaudes et
  fraîches*. **[À AJOUTER]** (filtres existants à étendre).
- **Section archivées** (périmées, gardées). **[À AJOUTER]**

## 8. État actuel vs cible — ce qui manque et pourquoi
- `Opportunity` **confond** entité + opportunité + contact → cible : séparer (ou
  au moins une clé d'entité + une notion de validité/péremption).
- Pas de **stage** ni de **fraîcheur** dérivés → à ajouter (fonctions pures).
- `reenrich` ne rafraîchit que Sirene → **généraliser** en refresh multi-canal.
- **Insta** (lecture + acquisition) **absent** → Phase 2 (Apify/search API, coût).
- **Scheduling** absent → Phase 2 (Postgres + hébergement).

## 9. Phasage proposé
1. **[À AJOUTER, maintenant]** fonctions pures `lifecycle_stage` + `freshness` +
   péremption (dérivées, testables) → badges/tri UI. Zéro dépendance externe.
2. **[À AJOUTER]** passe `refresh` multi-canal (Sirene+Places+scrape) émettant des
   signaux « vérifié le X » ; timeline UI ; section archivées.
3. **[À AJOUTER]** couche/clé **Entité** + merge-rule (réconciliation handle↔SIREN).
4. **[PHASE 2]** connecteur **Insta** (refresh + acquisition Insta-first) ;
   **scheduling** (cron/worker + Postgres/hébergement).

## 10. Décisions encore ouvertes (à trancher au plan)
- Table `Entity` séparée **vs** clé d'entité dérivée sur `Opportunity` (YAGNI).
- Seuils : X de staleness ; bornes d'avis/âge des stages.
- Une entité peut-elle porter **plusieurs opportunités** simultanées, ou une
  active à la fois ? (défaut proposé : une active ; l'historique reste en signaux).

## Tests à prévoir
- `lifecycle_stage` : chaque stade + cas limites (fresh 0 avis, établi 500 avis,
  fermé état=F).
- `freshness` / péremption : fraîche / à rafraîchir / périmée selon date + stage.
- refresh : émet un nouveau `Signal` sans supprimer les anciens ; recalcul du stage.
- merge-rule : handle→SIREN réconcilie sur la même entité.
