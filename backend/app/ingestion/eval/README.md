# Jeu de verite terrain - qualification des leads Instagram

But : mesurer la **precision** du pipeline Instagram (surtout le bucket "a contacter")
sur de **vrais comptes annotes a la main**, et regler les seuils sur des donnees
plutot qu'au doigt mouille. C'est le garde-fou anti-overfit : aucune regle n'est
consideree bonne tant qu'elle ne tient pas sur cet echantillon en hold-out.

## Fichier

`instagram_groundtruth.csv` - un compte par ligne, ouvert et juge a la main.
C'est une **cle de correction**, donc volontairement minimale et **agnostique au
modele** : elle ne contient QUE ce qu'on veut verifier (le label) + de quoi
joindre et justifier. Colonnes :

- `handle` : cle de jointure avec la sortie du pipeline.
- `name` : lisibilite humaine.
- `label` : la verite terrain (voir table ci-dessous). C'est LA colonne verifiee.
- `confidence` : high/med/low - permet d'exclure les cas ambigus du calcul strict.
- `provenance` : `opened_this_session` (observe direct) ou `prior_run` (moins fiable).
- `rationale` : pourquoi ce label (note libre, pas une feature structuree).

### Ce qui N'EST PAS dans ce fichier (volontaire)

Les signaux observables (posts_count, followers, horaires affiches, lien resa,
multi-adresses, mots-cles pre-ouverture, site...) **ne sont pas stockes ici**.
Raisons :
- ce sont les **entrees du modele** (le pipeline les scrape) - les mettre dans la
  cle de correction melangerait l'input et la verite ;
- ils **perissent** (les compteurs bougent) -> le fichier de verite pourrirait ;
- les avoir a cote du label **invite a l'overfit** (regler les seuils sur ce
  snapshot precis).

Le harness d'eval les (re)calcule au moment du test, a partir d'un scrape frais
ou d'un snapshot fige (cf. "Reproductibilite" plus bas). Le label, lui, reste
vrai quels que soient les features mesures.

### Labels (verite terrain)

| label | sens | bucket cible (brique 3bis) |
|---|---|---|
| `opening` | ouvre bientot, decor pas encore pose | a_contacter |
| `just_opened` | vient d'ouvrir, decor possiblement deja pose | a_surveiller |
| `established` | opere depuis des mois, decor fige | **en_base** (lead cree, segment froid) |
| `chain_multisite` | marque multi-sites, decor centralise | **en_base** (lead cree, segment froid) |
| `not_venue` | pas un etablissement CHR (agence, marque, hors secteur/pays) | **ecarte** (pas de lead) |
| `noise` | fast-food / compte mort / sans valeur | **ecarte** (pas de lead) |

Pivot inventaire (brique 3bis) : TOUT label devient un lead SAUF `not_venue`/
`noise`. `established`/`chain_multisite` (et `unknown`) tombent en **en_base**
(lead cree avec le signal neutre « etablissement en activite », score bas) — ce
ne sont plus des faux positifs a ecarter, juste le segment froid de l'inventaire.
Seuls `not_venue`/`noise` restent **ecarte** (aucun lead, cache seul).

### Metriques

- **Precision du segment chaud** (metrique HONNETE d'acceptation, brique 3bis) =
  vrais `opening`|`just_opened` parmi les predits `opening_soon`|`just_opened`.
  Un `just_opened` predit sur un vrai `just_opened` n'est **plus** un faux
  positif (l'ancienne « precision a_contacter » le comptait a tort). **Gate >=
  60 %.** On reporte systematiquement le denominateur (`hot_n`, nb de predits
  chauds) a cote du ratio : un segment chaud fin (~4-5 comptes) rend le chiffre
  volatil.
- **Rappel des opening** = `opening` retrouves en `a_contacter` / `opening`
  totaux. Espace de rappel inchange (`a_contacter`, projection binaire incluant
  `unknown`) -> **gate == 100 % (4/4), non negociable**.
- **Precision a_contacter** (metrique de continuite, briques 1-2) : toujours
  calculee et publiee, mais **NON bloquante** depuis la brique 3bis (le gate
  honnete est la precision du segment chaud).
- Matrice de confusion par label (verite mappee x label predit).

## Comment s'en servir

1. Faire tourner le pipeline (`discover` -> garde-fous deterministes (`profile_guards`)
   -> `judge_dossier` (juge unitaire)) sur ces memes `handle`, recuperer le
   verdict machine ; l'eval produit un LABEL par compte + une matrice de
   confusion label x label, en plus de la projection binaire a_contacter/ecarte.
2. Comparer verdict machine vs `label`. Metriques cles (cf. section Metriques) :
   - **precision du segment chaud** = vrais `opening`|`just_opened` / predits
     `opening_soon`|`just_opened` (LE chiffre honnete « opening = opening ? ») ;
   - rappel = `opening` retrouves / `opening` totaux (gate 4/4) ;
   - precision a_contacter (continuite, non bloquante) + matrice par label.
3. Regler les seuils (posts, followers, avis) et les regles deterministes en
   **train/test split**, jamais sur l'echantillon entier.

## Caveats honnetes (a lire avant d'y croire)

- **Taille : ~20 lignes = v1 indicatif, pas concluant.** Il faut viser 100-150
  comptes pour des seuils fiables.
- **Biais de collecte** : ces comptes viennent de mes chasses "opening soon" +
  des 4 leads de l'app. Sur-representation des ouvertures et des pieges que je
  cherchais. Il manque un tirage **aleatoire** de CHR etablis pour estimer le
  vrai taux de faux positifs.
- **Annotateur unique (moi)** : mes labels ne sont pas eux-memes valides. Idealement
  double annotation + cas ambigus (`confidence=low`) exclus du calcul strict.
- `provenance=prior_run` = compte non rouvert dans cette session (donnee de run
  precedent, fiabilite moindre) ; `opened_this_session` = observe directement.
- Les comptes evoluent (un `opening` finit `established`) : re-verifier le label
  si l'eval est rejouee longtemps apres (annotation faite le 2026-07-04).

## Reproductibilite

Comme les features ne sont pas dans la cle de correction, l'eval doit figer un
**snapshot** des profils au moment du test (ex. `eval/snapshots/<handle>.json`
= sortie brute du profile scraper) et faire tourner les regles dessus. Ainsi le
resultat est rejouable meme si un compte change ou disparait, sans jamais polluer
le fichier de verite.

## Prochaine etape

Agrandir a ~100-150 comptes avec un **echantillon aleatoire** de CHR (pas
seulement des ouvertures), puis brancher un script d'eval qui sort precision/
rappel/matrice. Tant que ce chiffre n'existe pas, toute regle de seuil reste une
hypothese.
