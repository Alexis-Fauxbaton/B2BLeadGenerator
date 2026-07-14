# Pipeline de qualification des contacts — design

Date : 2026-07-14
Statut : design (aucun code écrit). Cible : rendre le suivi de contact des
**closers** Ambient Home (qui appellent des architectes d'intérieur, + email +
DM Insta) intuitif et cross-canal, avec 2-3 niveaux de qualification saisissables
en **2 clics max au téléphone**. Décision produit d'Alexis : on **monitore les
résultats**, les issues n'écrivent JAMAIS sur la fiche automatiquement.

Contrainte transverse (critère d'acceptation) : **SOBRIÉTÉ**. On étend
l'existant (`ContactActivity`, `QuickActions`, `/activite`, `/followups`) sans le
dupliquer ni ajouter de fouillis.

---

## 0. Ce qui existe déjà (point de départ)

- `ContactActivity` (table `contact_activities`) : `type`
  (`appel|email|dm_insta|note|statut`), `note` libre, `author`, `created_at`.
  Journal SOBRE par fiche, découplé du statut (un geste ne change jamais le statut).
- `QuickActions` (ContactPanel.tsx) : boutons rapides. Les **issues d'appel
  existent déjà en 1 clic** mais sont stockées en texte libre dans `note`
  (« Répondu » / « Pas de réponse » / « Répondeur ») → **non agrégeable**. C'est
  précisément ce qu'on structure.
- `/activite` (admin SOFT) : journal global du jour + compteurs par closer.
- `/followups` : liste « À relancer » triée par urgence, liens `tel:` — **c'est
  la page d'accueil de fait du closer**.
- Migrations : uniquement `database._run_lightweight_migrations()` (ALTER TABLE
  ADD COLUMN conditionnel). Table déjà migrée une fois (`author`).

Principe directeur : `type` reste le **CANAL**. On empile par-dessus une couche
**issue → raison → détail** commune aux canaux. Cross-canal = même vocabulaire N1
partout ; le N2 est une table de correspondance indexée par `(type, issue)`.

---

## 1. Taxonomie cross-canal à 3 niveaux

> L'exemple d'Alexis (ok/ko → mauvais numéro/plus existant/pas intéressé) n'est
> pas figé. Écart assumé et justifié ci-dessous : **3 valeurs N1** au lieu de 2,
> parce que « pas joint » (à retenter) et « KO » (impasse) déclenchent des gestes
> opposés et parce que le **taux de joignabilité** (métrique clé du monitoring)
> exige de les séparer.

### N1 — Issue (universel, 3 valeurs)

| clé | libellé | couleur | sens | chemin closer |
|-----|---------|---------|------|---------------|
| `joint` | Joint | vert | on a eu un échange (décroché / réponse mail / réponse DM) | qualifier l'intérêt |
| `pas_joint` | Pas joint | ambre | tentative sans retour | **retenter** (relance J+N) |
| `ko` | KO | rouge | impasse sur ce canal / ne pas recontacter | abandonner le canal |

Décision : **« pas intéressé » vit sous `joint`**, pas sous `ko`. Raison : joindre
quelqu'un qui dit non EST une tentative aboutie (elle compte dans la
joignabilité). Un refus **ferme** (« ne me rappelez plus ») se marque `ko` /
`ne_plus_contacter`. On explique ce choix à Alexis car il diverge de son exemple.

`issue` est **optionnel** : un « Email envoyé » / « DM envoyé » est une **action
d'émission** sans résultat encore connu → `issue = NULL`. Le résultat (réponse /
bounce) se logge plus tard en une 2ᵉ activité. Pour l'appel, émission et résultat
sont simultanés → une seule activité porte l'issue.

### N2 — Raison (par `(type, issue)`, adaptée au canal)

```
APPEL
  joint     → interesse (Intéressé) · a_rappeler (À rappeler) · pas_interesse (Pas intéressé)
  pas_joint → repondeur (Répondeur) · pas_de_reponse (Pas de réponse) · occupe (Occupé)
  ko        → mauvais_numero (Mauvais numéro) · ferme (N'existe plus / fermé) · ne_plus_contacter (Ne pas recontacter)

EMAIL
  joint     → interesse (Réponse intéressée) · a_suivre (Réponse à suivre) · pas_interesse (Pas intéressé)
  pas_joint → pas_de_reponse (Pas de réponse)
  ko        → bounce (Adresse invalide) · desinscription (Stop / désinscription)

DM_INSTA
  joint     → interesse (Réponse intéressée) · a_suivre (Réponse à suivre) · pas_interesse (Pas intéressé)
  pas_joint → vu_sans_reponse (Vu, sans réponse) · pas_de_reponse (Pas de réponse)
  ko        → compte_introuvable (Compte fermé / introuvable) · bloque (Bloqué)
```

`raison` est optionnel (un `issue` seul est valide). Validé serveur : si fourni,
doit appartenir au set autorisé de `(type, issue)` — sinon 422 (même politique
que la validation de `type` existante).

### N3 — Détail (chips optionnelles + note libre)

Chips réutilisables, surtout pertinentes sous `pas_interesse` / `a_rappeler` :

```
deja_fournisseur (A déjà un fournisseur) · pas_de_projet (Pas de projet) ·
budget (Budget) · mauvais_interlocuteur (Mauvais interlocuteur) ·
rappeler_plus_tard (Rappeler plus tard)
```

+ **note libre** (champ `note` existant). N3 est **toujours optionnel**, jamais
requis, jamais bloquant. Peu agrégé (couleur/contexte) ; le monitoring vit surtout
en N1/N2.

### Source de vérité unique

Un dict `QUALIF_TAXONOMY` dans `models.py` (backend = autorité de validation),
**miroir** dans `frontend/lib/labels.ts` (libellés FR + presets). Forme :

```python
# models.py — pseudocode
QUALIF_ISSUES = ["joint", "pas_joint", "ko"]  # N1
QUALIF_RAISONS = {                              # N2 : (type, issue) -> [raisons]
    ("appel", "joint"): ["interesse", "a_rappeler", "pas_interesse"],
    ("appel", "pas_joint"): ["repondeur", "pas_de_reponse", "occupe"],
    ("appel", "ko"): ["mauvais_numero", "ferme", "ne_plus_contacter"],
    # … email, dm_insta …
}
QUALIF_DETAILS = ["deja_fournisseur", "pas_de_projet", "budget",
                  "mauvais_interlocuteur", "rappeler_plus_tard"]  # N3
```

---

## 2. Monitoring des résultats (Alexis) — SANS effet de bord

Décision verbatim : « les réponses nourrissent pas la donnée, on veut surtout
monitorer les résultats ». **Invariants non négociables** :

- Une issue est **enregistrée + agrégée**, elle ne modifie JAMAIS la fiche :
  pas de flag téléphone posé, pas de fermeture auto, pas de statut auto, aucun
  champ pré-rempli. Le geste de qualification et le changement de statut restent
  **découplés** (déjà le cas aujourd'hui — on préserve).
- Le tri / l'affichage des listes peuvent **LIRE** la dernière issue (voir §2.2),
  jamais **ÉCRIRE** sur la fiche.

### 2.1 Vue de monitoring (dans `/activite`, sobre)

`/activite` gagne un **toggle segmenté en tête** : `Journal` (l'existant, inchangé)
| `Résultats` (nouveau). Pas de nouvelle page dans la nav → zéro clutter.

Onglet **Résultats** (admin SOFT, mêmes gardes que le journal) :

1. **Sélecteur de période** : presets `Aujourd'hui` / `7 jours` / `30 jours`
   + dates libres (réutilise le pattern de sélecteur de jour existant).
2. **Bandeau de KPIs** (tuiles sobres, style `StatCard` déjà présent) :
   - Tentatives (activités avec `issue` non nul sur la période)
   - **Taux de joignabilité** = `joint / (joint + pas_joint + ko)`
   - Volume d'appels (activités `type=appel`)
   - Réponses email + DM
3. **Par closer** : petit tableau `closer | tentatives | joints | joignabilité`.
4. **Par canal** : appel / email / DM (compteurs + joignabilité par canal).
5. **Top raisons de KO** : `group by raison where issue='ko'`, top 5, en barres
   horizontales sobres.
6. **Volume d'appels par jour** : mini barres (7 ou 30 barres) — repère de rythme.

Tout est **lecture agrégée** ; aucune action, aucun bouton d'écriture sur cet
écran.

### 2.2 Lecture de la dernière issue par les listes (affichage seul)

`/followups` (et optionnellement la liste `/opportunities`) affichent une **puce
discrète** de la dernière issue (« dernier contact : Répondeur », « Mauvais
numéro ») pour prioriser à l'œil. **Jamais persisté sur la fiche.**

Implémentation sobre (évite le N+1) : endpoint batch
`GET /api/opportunities/last-issues?ids=1,2,3` → `{ opp_id: {issue, raison, at} }`,
appelé avec les ids de la page courante (N petit par page). Réponse dérivée à la
volée (dernière `contact_activity` avec `issue` non nul par fiche). Intégration
`/followups` = recommandée ; intégration liste complète = optionnelle/plus tard.

> Note : `mauvais_numero` **n'allume aucun flag** sur la fiche. Il se lit comme
> puce et peut nourrir un tri « à vérifier » côté liste, sans écriture.

---

## 3. Parcours closer (débutant, sans formation)

### En arrivant : `/followups` = « Ma journée »

Déjà en place et adapté : liste triée par urgence (en retard / aujourd'hui /
cette semaine), lien `tel:` tapable, ligne cliquable vers la fiche. **Ajout
minimal** : la puce « dernière issue » (§2.2) pour savoir d'un coup d'œil où on
en était. Gros targets tactiles conservés.

### Pendant / après l'appel : la barre de qualification (sur la fiche)

`QuickActions` devient une **barre de qualification canal-aware**. Layout :

```
[ Canal : ● Appel   ○ Email   ○ DM ]        (défaut = Appel ; ou recommended_channel)

  JOINT (vert)        PAS JOINT (ambre)        KO (rouge)
  [Intéressé]         [Répondeur]              [Mauvais numéro]
  [À rappeler]        [Pas de réponse]         [N'existe plus]
  [Pas intéressé]     [Occupé]                 [Ne pas recontacter]

  ＋ détail            (expander optionnel : chips N3 + note libre)
```

- **Chemin rapide (90 % des appels) = 1 tap** : chaque chip est un preset qui
  écrit `{type, issue, raison}` en **un seul POST**. Presets groupés et colorés
  par N1 → le closer lit la couleur, tape la bonne case. **2 clics max** (choix
  canal éventuel + 1 tap), souvent 1.
- **Chemin détaillé (rare) = opt-in** : « ＋ détail » déplie les chips N3 + la
  note ; on valide alors `{type, issue, raison, detail[], note}` en **un seul
  POST** aussi (pas de PATCH, pas de 2ᵉ écriture).
- Après log : les **boutons de relance rapide J+3 / J+7** existants restent
  dessous (le geste « ça n'a pas répondu → J+3 » reste à portée). La relance est
  un choix **explicite** du closer, jamais auto-déclenché par l'issue.
- Émission email / DM : boutons « Email envoyé » / « DM envoyé » conservés →
  activité `issue=NULL`. Le résultat se logge plus tard via la même barre en
  basculant le canal.

### Le journal de la fiche

`ActivityTimeline` affiche désormais **issue + raison en badge** coloré (vert /
ambre / rouge) au lieu d'un texte brut, + note/chips en dessous. Plus lisible,
même densité.

### Ses relances

Inchangé : `/followups` + `NextActionCard`. La qualification ne pilote pas la
relance ; elle l'**informe** (via la puce dernière issue).

---

## 4. Modèle de données (migration légère)

### `contact_activities` : 3 colonnes ajoutées

```python
# database._run_lightweight_migrations() — bloc contact_activities
"issue":  "ALTER TABLE contact_activities ADD COLUMN issue VARCHAR",   # N1
"raison": "ALTER TABLE contact_activities ADD COLUMN raison VARCHAR",  # N2
"detail": "ALTER TABLE contact_activities ADD COLUMN detail JSON",     # N3 (list[str])
```

`models.ContactActivity` gagne : `issue: Optional[str] = None`,
`raison: Optional[str] = None`, `detail: List[str] = Field(default_factory=list,
sa_column=Column(JSON))`. `type` (canal) et `note` (texte libre) inchangés.

Rétro-compat : les anciennes lignes (`type=appel`, `note="Répondu"`, `issue=NULL`)
restent valides ; le monitoring n'agrège que les lignes `issue` non nul (démarrage
« propre »). **Pas de backfill** recommandé (volume faible, sobriété) ; un mapping
one-shot texte→issue reste possible plus tard si besoin.

### `opportunities` : PAS de `phone_invalid` maintenant

Item 4 le liste comme « éventuel ». **Recommandation : ne pas l'ajouter** — il
violerait l'invariant « les issues n'écrivent jamais sur la fiche » dès qu'on le
poserait automatiquement. Le besoin (« repérer un mauvais numéro ») est déjà
couvert en **lecture** par §2.2 (`issue=ko / raison=mauvais_numero`). Si Alexis
veut plus tard un flag durable, il devra être **posé manuellement et
explicitement** par le closer (action distincte, hors pipeline d'issue) — à
concevoir à ce moment-là, pas dans ce chantier.

### Schémas (`schemas.py`)

- `ContactActivityCreate` : `+ issue?, raison?, detail: list[str] = []`.
- `ContactActivityRead` : idem exposé en lecture.
- Nouveaux : `QualifStats` (KPIs + par closer + par canal + top KO + volume/jour),
  `LastIssue` (issue/raison/at pour le batch).

### Validation (`routes/activities.py`)

Dans `add_activity`, après le contrôle `type` existant : si `issue` fourni →
∈ `QUALIF_ISSUES` ; si `raison` fourni → ∈ `QUALIF_RAISONS[(type, issue)]` ;
`detail` → sous-ensemble de `QUALIF_DETAILS`. Sinon 422. `author` : la session
prime toujours (règle existante conservée). **Toujours aucune écriture de statut.**

---

## 5. Écrans à créer / modifier (réutilisation, zéro duplication)

**Backend**
- `models.py` : `+ QUALIF_ISSUES / QUALIF_RAISONS / QUALIF_DETAILS` ; champs
  `issue/raison/detail` sur `ContactActivity`.
- `database.py` : 3 ADD COLUMN dans le bloc `contact_activities` existant.
- `schemas.py` : `ContactActivityCreate/Read` étendus ; `QualifStats`, `LastIssue`.
- `routes/activities.py` : validation issue/raison/detail ; endpoint batch
  `GET /api/opportunities/last-issues`.
- `routes/activite.py` : endpoint agrégé `GET /api/activite/stats`
  (période → KPIs / par closer / par canal / top KO / volume par jour).

**Frontend**
- `lib/types.ts` : `ContactActivity` (+issue/raison/detail) ; `QualifStats`,
  `LastIssue`.
- `lib/labels.ts` : **miroir de la taxonomie** — libellés + couleurs N1, table
  N2 `(type,issue)`, chips N3, presets par canal, helper `issueBadge`.
- `lib/api.ts` : `addActivity` accepte issue/raison/detail ; `getActivityStats`,
  `getLastIssues`.
- `components/ContactPanel.tsx` : `QuickActions` → **`QualificationBar`**
  (segmenté canal + presets colorés N1/N2 + expander N3/note, un seul POST) ;
  `ActivityTimeline` rend les badges issue/raison.
- `app/opportunities/[id]/page.tsx` : consomme la barre remaniée (changement
  minimal, la section « Suivi de contact » existe déjà).
- `app/activite/page.tsx` : toggle `Journal | Résultats` + section monitoring.
- `app/followups/page.tsx` : puce « dernière issue » (affichage seul) ; liste
  `/opportunities` = optionnel/plus tard.

**Tests** (étendre `tests/test_contact_activities.py`, garder tout vert)
- validation issue/raison/detail (accept + 422 sur combo invalide) ;
- issue optionnelle (émission `issue=NULL`) ;
- migration : présence des 3 colonnes sur table existante ;
- agrégats stats (joignabilité, top KO, volume/jour) ;
- batch last-issues ; **non-régression : aucune écriture de statut/fiche par un
  geste de qualification.**

---

## Récap des décisions de conception

1. `type` = canal ; couche `issue/raison/detail` par-dessus → cross-canal natif.
2. N1 à 3 valeurs (joint / pas_joint / ko), pas 2 → joignabilité correcte + gestes
   distincts. « Pas intéressé » ∈ `joint`. Écart assumé vs l'exemple d'Alexis.
3. `issue` optionnel → émission email/DM = action sans résultat (`NULL`).
4. Chemin rapide 1 tap (preset issue+raison, 1 POST) ; détail N3 opt-in, même POST.
5. Monitoring dans `/activite` (toggle), 100 % lecture, aucun effet de bord.
6. **Pas de `phone_invalid`** : couvert en lecture (§2.2), respecte l'invariant.
7. 3 colonnes ADD COLUMN, aucune table neuve, aucun Alembic.
