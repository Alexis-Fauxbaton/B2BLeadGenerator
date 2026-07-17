# Numéros candidats multiples — design (2026-07-17)

## Contexte & problème

Un lead (surtout architecte d'intérieur) expose souvent **plusieurs numéros
potentiels** selon la source. Cas réel fiche 6783 :

- l'annuaire publie `02 49 88 42 85` (posé à l'ingestion, `raw['phone']`) ;
- le site du studio affiche `02 85 52 84 93` (vu par `scrape_phone`).

Aujourd'hui on n'en garde **qu'un** et on jette le reste :

- `website_scraper.choose_phone` : le premier palier non vide décide ; s'il est
  **ambigu** (≥ 2 numéros distincts) il renvoie `None` — les numéros sont perdus.
- `enrich_phones` : ne remplit `phone` que si vide (VIDE > FAUX) ; les paliers
  moins sûrs sont abandonnés.
- ingestion annuaire (`pipeline._process_candidate`, l.1270) et fusion douce
  (l.1208) : `existing.phone = existing.phone or cand.raw['phone']` — un numéro
  annuaire **différent** d'un `phone` déjà présent est **ignoré**.
- `_merge_corroboration` (cross-fill) : ne touche pas au téléphone du tout.

Le closer ne voit donc jamais le 2ᵉ numéro, ne peut pas le tenter, et quand il
tombe sur un mauvais numéro il n'a aucun repli.

### Objectif

1. Voir les **candidats** avec leur provenance (site / annuaire / places /
   cross-fill).
2. Chaque tentative de contact enregistre **quel** numéro / email / handle a
   été tenté (`ContactActivity.contact_used`).
3. **Promouvoir** un candidat en principal — action **manuelle** du closer,
   **tracée**. JAMAIS de mutation automatique par une issue de qualification
   (règle établie : « on monitore, on ne nourrit pas la donnée »).

Le champ `Opportunity.phone` **reste LE champ de référence** : listes d'appel,
`tel:`, corroboration, dédup — tout ce qui existe est inchangé. Les candidats
sont une couche **additive** « à tester ».

---

## 1. Modèle

### 1.1 Colonne `phone_candidates` (JSON) sur `opportunities`

```python
# models.py — Opportunity, à côté de extra_addresses/extra_emails
# Numéros ALTERNATIFS « à tester » (le principal reste `phone`). Chaque entrée :
# {number, source, proof_url?, first_seen}. Jamais un doublon (forme normalisée)
# du principal ; cap à 5 ; alimentés par les producteurs (site/annuaire/places/
# cross-fill) et réordonnés par la promotion manuelle. AUCUN impact sur les
# listes d'appel (qui lisent `phone`).
phone_candidates: List[dict] = Field(default_factory=list, sa_column=Column(JSON))
```

Forme d'une entrée (dict JSON, pas de sous-table — même parti-pris que
`dirigeants`/`extra_emails`) :

| clé         | type            | sens                                                              |
|-------------|-----------------|-------------------------------------------------------------------|
| `number`    | str             | numéro **normalisé** (`normalize_phone` : FR `0X XX XX XX XX` ou Monaco `+377 …`) |
| `source`    | str (enum)      | `site` \| `annuaire` \| `places` \| `cross_fill` \| `ex_principal` |
| `proof_url` | Optional[str]   | URL de la source si connue (site scrapé, fiche annuaire) ; sinon absent |
| `first_seen`| str (ISO date)  | date de première apparition (traçabilité, tri stable)             |

`ex_principal` = 5ᵉ provenance, posée **uniquement** par la promotion quand
l'ancien principal redevient candidat : on ne connaît pas toujours sa provenance
d'origine (le principal ne stocke aujourd'hui que `contact_confidence`, pas sa
source), `ex_principal` est donc l'étiquette honnête (« ancien principal »).

### 1.2 Invariants (helper pur, réutilisé partout)

Toute la logique vit dans **un module pur testable sans réseau** :
`app/services/phone_candidates.py` (les producteurs sont dispersés dans
`ingestion/`, l'endpoint de promotion dans `routes/` — un point unique évite
trois implémentations divergentes ; `models.py` reste data-only).

```python
def add_candidate(opp, number, source, proof_url=None, *, today=None) -> bool:
    """Ajoute un numéro candidat à `opp.phone_candidates`, en place, si (et
    seulement si) il est neuf et utile. Renvoie True s'il a été ajouté.

    Règles (VIDE > FAUX ne s'applique PAS aux candidats : un candidat douteux
    est explicitement « à tester », pas affiché comme certain) :
      - normalisation : `normalize_phone(number)` ; None (motif implausible)
        -> rejeté (on ne stocke pas un candidat qu'on ne sait pas appeler) ;
      - jamais un doublon du PRINCIPAL : si la forme normalisée == `opp.phone`
        normalisé -> rejeté ;
      - jamais un doublon entre candidats (comparaison sur `number` normalisé) ;
      - cap DUR à 5 : au-delà, le nouveau est ignoré (un lead à > 5 numéros
        distincts est du bruit) ; l'ordre first_seen est préservé.
    """
```

`phone` reste la seule vérité pour les listes d'appel. `add_candidate` ne touche
**jamais** `phone`. Réciproquement, quand un producteur écrit `phone` (fiche
vide), il **ne** crée **pas** de candidat pour ce même numéro.

### 1.3 Migration légère

`database._run_lightweight_migrations`, dict `additions` (table `opportunities`) :

```python
"phone_candidates": "ALTER TABLE opportunities ADD COLUMN phone_candidates JSON",
```

NULL sur les lignes existantes → coercé en `[]` à la sérialisation (validateur
`_coerce_none_list` de `OpportunityList`, y ajouter `phone_candidates`). Aucun
backfill (démarrage propre, comme la qualif v2).

---

## 2. Producteurs — conserver au lieu de jeter

### 2.1 Site (`website_scraper` + `enrich_phones`)

`choose_phone` reste **inchangé** (il décide du principal ; des centaines de
tests en dépendent). On **ajoute** une fonction sœur pure :

```python
def collect_phone_candidates(pages) -> List[str]:
    """Numéros normalisés distincts vus sur le site, PRIVÉS de celui que
    `choose_phone` a retenu (s'il a décidé) et des TEMPLATE_JUNK_PHONES.
    Ordre : tel: home d'abord, puis contact, puis texte. Sert à peupler les
    candidats site quand le site expose plusieurs numéros (ambiguïté = pile le
    cas où l'ancien code jetait tout)."""
```

Dans `enrich_phones._enrich_one_phone` : après le waterfall, quel que soit le
palier gagnant, on récupère les numéros site restants et on les pousse en
candidats `source='site'`, `proof_url=<url du site qui a répondu>`. Le principal
suit la règle VIDE > FAUX inchangée ; les écartés par **ambiguïté** ou par
**palier** deviennent candidats (au lieu d'être perdus). La garde inter-domaines
(`cross_domain_junk`) s'applique aussi aux candidats : un numéro de démo de
template partagé ne doit pas non plus devenir candidat.

`scrape_phone` gagne un wrapper mince `scrape_phones(url) -> {principal, candidates}`
qui réutilise le même fetch (pas de double requête réseau) ; `scrape_phone`
délègue à lui pour rester identique.

### 2.2 Places / OSM

`enrich_phones._phone_from_places` renvoie déjà `(numéro, basis)`. Si le numéro
Places **diffère** du principal retenu au palier site, on le pousse en candidat
`source='places'` (pas de `proof_url` fiable → absent). Idem côté pipeline pour
la source `places` : quand `cand.raw['phone']` diffère de `existing.phone`, le
combler-si-vide reste, mais le **cas différent** ajoute un candidat au lieu de
l'ignorer.

### 2.3 Annuaire (ingestion) & cross-fill

Point unique du changement dans `pipeline._process_candidate` : les deux lignes
`existing.phone = existing.phone or cand.raw['phone']` (upsert même-source,
l.1270) et `soft.phone = soft.phone or cand.raw['phone']` (fusion douce, l.1208)
deviennent :

```python
_num = cand.raw.get("phone")
if _num:
    if not existing.phone:
        existing.phone = _num                     # comble le principal (inchangé)
    else:
        add_candidate(existing, _num, "annuaire",  # DIFFÉRENT -> candidat
                      proof_url=cand.proof_url)     # (no-op si == principal)
```

`add_candidate` étant idempotent (dédup principal + candidats), on peut l'appeler
inconditionnellement : quand le numéro **égale** déjà le principal il est rejeté
sans effet. Cross-fill (`_merge_corroboration`) : ajouter en fin de fonction le
même appel `add_candidate(opp, cand.raw.get('phone'), 'cross_fill', …)` — la
fusion ne perd donc plus le téléphone de l'autre source.

> **VIDE > FAUX reste la règle pour LE PRINCIPAL.** Les candidats, eux, sont
> explicitement « à tester », affichés avec leur provenance : on assume un
> numéro incertain **tant qu'il est étiqueté**, jamais promu en silence.

---

## 3. `ContactActivity.contact_used`

### 3.1 Colonne + schémas

```python
# models.py — ContactActivity
# Contact EFFECTIVEMENT tenté au moment du geste (numéro affiché / email /
# handle DM). Auto-rempli à la saisie côté UI ; sert au monitoring (« quel
# numéro a été tenté ») et à la suggestion visuelle sur « mauvais numéro ».
# N'écrit JAMAIS sur la fiche (même invariant que issue/raison/detail).
contact_used: Optional[str] = None
```

Migration : dict `ca_additions` de `_run_lightweight_migrations` →
`"contact_used": "ALTER TABLE contact_activities ADD COLUMN contact_used VARCHAR"`.
`ContactActivityCreate` + `ContactActivityRead` + type TS `ContactActivity`
gagnent `contact_used?: string | null`. Aucune validation d'enum (c'est une
valeur libre : le contact affiché au moment de l'appel).

### 3.2 Sémantique

- **auto-rempli** à la saisie : `QualificationBar` préremplit `contact_used`
  avec le contact du canal courant — `phone` (principal) pour `appel`, `email`
  pour `email`, `@instagram` pour `dm_insta` — et le laisse **modifiable** (voir
  §5.2). La session prime sur le body comme pour `author` : n'est retenu que ce
  que l'UI envoie (pas d'auth ici).
- **« Mauvais numéro »** (`raison='mauvais_numero'`) porte sur **CE**
  `contact_used`, pas sur la fiche. L'UI propose alors le **candidat suivant**
  (suggestion visuelle) — **aucune mutation auto** du principal ni des candidats.
  Le closer reste seul maître de la promotion.

---

## 4. Promotion manuelle

### 4.1 Endpoint

`POST /api/opportunities/{id}/phones/promote`, body `{ "number": "02 85 52 84 93" }`
(schéma `PhonePromote`). Logique (dans le helper pur `promote(opp, number, …)`
pour être testable) :

1. normaliser `number` ; 404-like 422 si absent des candidats ;
2. l'ancien `opp.phone` (s'il existe) redescend en candidat `source='ex_principal'`,
   `first_seen=today` ;
3. le candidat promu **quitte** `phone_candidates` et devient `opp.phone` ;
4. re-dédup (le nouveau candidat ex_principal ne doit pas dupliquer un candidat
   déjà là) ; cap 5 réappliqué ;
5. `opp.updated_at = now` (la fiche vit).

**`contact_confidence` : inchangé par la promotion.** Justification : ce champ
décrit la *méthode de vérification* de la provenance (`haute` = match géo,
`moyenne` = nom+ville, `basse` = repli) — pas la préférence d'un humain. Le
surcharger mentirait sur la sémantique et fausserait des filtres existants. La
promotion est un choix humain, tracé par l'activité `note` (§4.2) ; c'est la
trace, pas la confiance, qui atteste le geste. Conséquence : voir §5.1 — le
principal est désormais **toujours affiché** (avec sa puce de confiance
inchangée), sinon un principal fraîchement promu sur une fiche `confidence !=
haute` resterait masqué par l'ancienne garde d'affichage.

### 4.2 Trace : activité auto de type `note` (décision)

**Oui**, la promotion crée automatiquement une `ContactActivity` de type
**`note`** :

```
note = "Numéro principal changé : 02 49 88 42 85 → 02 85 52 84 93 (source site)"
```

Justification du choix `note` (vs. un nouveau type ou `statut`) :

- `note` **existe déjà** dans `ACTIVITY_TYPES` (icône `StickyNote`, rendu FR
  dans `ActivityTimeline`) → **aucun** nouveau type, enum, migration ni libellé.
- `statut` est réservé au journal AUTO des changements de `status` (« ancien →
  nouveau » rendu par `frStatusNote`) — le réutiliser pour un numéro
  brouillerait ce miroir.
- La trace apparaît **dans le même journal** que le closer lit déjà ; `author`
  = session (comme `add_activity`) → on sait **qui** a promu.
- `issue`/`raison`/`detail` restent **null** : ce n'est pas une qualification.
  On respecte ainsi « jamais de mutation par une issue » **dans les deux sens** :
  la promotion (mutation manuelle légitime) n'emprunte pas le canal
  qualification, et la qualification ne mute jamais la fiche.

C'est le **seul** endroit où une action produit à la fois une mutation de fiche
(`phone`/`phone_candidates`) et une activité — cohérent car c'est un geste
**manuel** explicite, pas une déduction automatique.

---

## 5. UI (SOBRE — « pas le fouillis »)

### 5.1 Fiche — bloc Contact (`ContactBlock`, page `[id]`)

- **Principal en gros** : `opp.phone` avec sa puce de confiance
  (`ConfidenceChip`) et l'action « Appeler » (inchangé), **toujours rendu s'il
  existe** (nouvelle garde : `opp.phone` présent, plus `contact_confidence ==
  'haute'` — cf. §4.1). Le repli « à trouver » ne s'affiche que si `phone` est
  vide ET aucun candidat.
- **Candidats discrets** dessous : liste compacte, chacun =
  `numéro` + petit **badge de provenance** (Site / Annuaire / Places /
  Cross-fill / Ancien principal) + lien `tel:` + bouton **« Promouvoir »**
  (icône `ArrowUp`/`Star`). Style secondaire (texte slate, pas de gros CTA) :
  ce sont des pistes, pas le contact retenu. Un libellé « à tester » chapeaute
  la liste.
- Promotion : `api.promotePhone(id, number)` → refetch fiche + activités
  (l'activité `note` apparaît dans la timeline).
- `proof_url` : le badge de provenance est un lien vers la preuve quand elle
  existe (site scrapé / fiche annuaire).

### 5.2 Grille de qualification (`QualificationBar`)

- Un sélecteur **`contact_used`** discret, préaffiché sur le contact du canal
  courant, **modifiable** :
  - `appel` : dropdown `{ principal + candidats }` (numéros) ;
  - `email` : `{ email + extra_emails }` ;
  - `dm_insta` : `@instagram` (souvent unique → simple libellé, pas de dropdown).
  La valeur choisie part dans le body `addActivity` (`contact_used`).
- Sur un tap **« Mauvais numéro »** (`ko`/`mauvais_numero`) : après le POST, si
  des candidats restent, afficher une **suggestion visuelle** « Essayer plutôt :
  `<candidat suivant>` » avec un bouton qui **présélectionne** ce candidat comme
  `contact_used` du prochain geste — **jamais** de promotion ni de mutation
  automatique. Simple aide, une ligne.
- `QuickQualifyPopover` (liste `/followups`) : `contact_used` défaut = principal,
  non éditable (contexte liste, on garde 2 clics). Le détail fin reste sur la
  fiche.

### 5.3 Monitoring (`/activite`) — inchangé

La vue patron n'est pas modifiée par ce chantier. `contact_used` est stocké et
exposé en lecture ; son exploitation analytique (« taux de mauvais numéro par
source ») est **hors périmètre**, prête pour plus tard.

---

## 6. Tests & gates (pytest complet vert, 830+)

- **Helper `phone_candidates`** (pur, sans réseau) : dédup vs principal, dédup
  entre candidats, rejet numéro implausible, cap 5, normalisation FR/Monaco,
  idempotence de `add_candidate`, `promote` (swap + ex_principal + re-dédup).
- **`collect_phone_candidates`** : sur les fixtures HTML existantes (fiche 6783
  reconstituée : site + annuaire → 1 principal + 1 candidat de l'autre source).
- **Producteurs** : `enrich_phones` pousse les écartés en candidats ; ingestion
  annuaire ajoute un candidat quand `raw['phone']` diffère (et **no-op** quand
  il est égal) ; cross-fill ajoute `cross_fill`. **Gate de non-régression** :
  `phone` (principal) et les listes d'appel **identiques** avant/après sur les
  fixtures CHR/A1/A2 (le principal ne bouge jamais tout seul).
- **Endpoint promote** : 200 + swap correct + activité `note` créée (author =
  session), 422 si `number` hors candidats, `contact_confidence` intact.
- **`add_activity`** : `contact_used` persisté et relu ; toujours aucun effet
  sur `status`/fiche.
- **Frontend** : `npm run build` vert ; UI FR ; bloc candidats caché quand la
  liste est vide (fiches sans candidat = affichage strictement inchangé).

## 7. Ordre d'implémentation

1. Migration + colonnes (`phone_candidates`, `contact_used`) + schémas + types TS.
2. `services/phone_candidates.py` (helper pur) + tests unitaires.
3. `website_scraper.collect_phone_candidates` / `scrape_phones` + tests.
4. Câblage producteurs (`enrich_phones`, `pipeline` annuaire/places/cross-fill)
   + gate de non-régression du principal.
5. Endpoint `promote` + activité `note` auto + tests.
6. UI fiche (candidats + promouvoir) puis `QualificationBar` (`contact_used` +
   suggestion « mauvais numéro »).

## 8. Écarts assumés vs. la demande

- **`ex_principal`** ajouté à l'enum des sources (la lettre en listait 4) : sans
  lui, un ancien principal démoté n'a pas de provenance honnête. Justifié §1.1.
- **Affichage : principal toujours montré** (au lieu de la garde `confidence ==
  'haute'`) : conséquence directe de « `contact_confidence` inchangé par la
  promotion » (§4.1) — sinon un numéro promu par un humain pourrait rester
  invisible. Changement délibéré et cohérent avec l'esprit (« voir les
  candidats, pouvoir les tenter »). Les fiches **sans** candidat retrouvent le
  comportement d'avant dès que `phone` est présent.
