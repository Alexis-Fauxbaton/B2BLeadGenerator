# Handoff - Eval precision des leads Instagram (brief pour Claude Code)

Ce fichier resume une session d'analyse et te confie la suite. Lis-le en entier
avant de coder. Fichiers deja crees dans ce dossier : `instagram_groundtruth.csv`
(cle de correction) + `README.md` (methodo). Ta tache principale = le **harness
d'eval** (section "Ta tache").

## 1. Contexte

App `chr-signal-radar` : detecte des opportunites CHR (cafes/hotels/restos) pour
un fournisseur de luminaires. Source Instagram dans
`backend/app/ingestion/instagram.py`, pipeline en 3 etages :
`discover` (heuristique CHR + IdF) -> `judge` (LLM : is_venue_owner + freshness)
-> `profile_enrich` (scrape profil, drop les etablis).

Cas d'usage metier reel : identifier des lieux **en pre-ouverture, decor pas
encore pose** (le bon moment pour vendre des luminaires), et les separer du reste.

## 2. Le probleme central (valide empiriquement)

Question posee : "les 'opening soon' sont-ils vraiment des opening soon ?"
On a ouvert a la main les 4 leads Instagram de l'app. Resultat :

- `tregusto_sartrouville` (4 posts, 58 abo) -> VRAIE ouverture. OK.
- `cafe_mokaparis` (46 posts, 5598 abo, **3 adresses en bio**, "open everyday")
  -> classe "creation recente" alors que c'est une **chaine deja ouverte**. FAUX POSITIF.
- `chickntikka94` (2 posts, 1 abo, fast-food, pas de bio) -> techniquement "frais"
  mais **sans valeur** (bruit), score 6/10.
- `giorgina_restaurant` -> ambigu, et ville = "Giorgina restaurant" (champ casse).

=> Le label "opening soon" n'est PAS fiable. C'est le point le plus important du
produit et il n'est aujourd'hui pas mesure.

## 3. Bugs concrets trouves (avec preuves via l'API locale)

1. **Reco de canal ignore le handle** (`services/channel_recommendation.py`) :
   les 4 leads IG ont un handle mais `recommended_channel = "telephone"` ; et
   **18 leads** ont `channel = instagram` avec `instagram = null` (DM vers
   personne). Cause : `has_social_presence` n'est jamais alimente par la presence
   du champ `instagram`. Effet de bord scoring : `+1 "canal clair"` va aux faux.
2. **Extraction de ville cassee** (`instagram.py::_city_from_location`) : defaut
   silencieux "Paris" ; "Giorgina restaurant" ecrit dans le champ ville.
3. **Verrouillage Ile-de-France** (`IDF_HINTS`, hashtags 100% paris) alors que le
   besoin metier est **France entiere**.
4. **Aucun filet deterministe "deja ouvert / chaine / fast-food"** : tout repose
   sur le juge LLM ; le seul garde-fou dur est `POSTS_ESTABLISHED_HARD = 150`
   (trop haut : MOKA passe dessous). Signaux presents dans le profil mais non
   exploites : horaires/"open everyday", lien resa, **>=2 adresses en bio**
   (=chaine), reviewsCount/followers eleves.
5. **`judge()` batche tous les candidats** dans un prompt (risque de contamination)
   alors que `profile_enrich` est deja passe en appels unitaires. A aligner.
6. **Zero observabilite** : `except Exception: return []/{}` partout, impossible de
   distinguer "0 lead" de "token/quota KO".

## 4. Direction produit decidee

- Passer d'un tri **binaire** (garde/jette) a un tri en **buckets** qui reprend
  les statuts de la veille manuelle :
  `a_contacter / a_surveiller / a_confirmer / a_reverifier / ecarte`.
- Mettre des **regles deterministes causales DEVANT le LLM** (elles rattrapent
  quand le LLM se trompe) :
  - deja ouvert (horaires/resa/avis) -> a_surveiller ;
  - >=2 adresses en bio ou marque connue -> ecarte (chaine, decor centralise) ;
  - fast-food/traiteur/dark-kitchen -> ecarte ;
  - ville/dirigeant/SIREN non prouves -> a_confirmer ;
  - pas de canal joignable -> a_reverifier.
- Garder le LLM pour le flou (fraicheur, is_venue_owner) : il generalise mieux
  que des regex.

## 5. Discipline anti-overfit (IMPORTANT)

On a explicitement acte que les **seuils chiffres** proposes (baisser 150 -> 40,
seuils followers/avis, "2 adresses") sont des **hypotheses derivees de 4 exemples**
= overfit tant que non valides. Regles du jeu :

- Les signaux observables sont des **features candidates**, PAS des regles en dur.
- Les seuils se reglent sur le jeu de verite terrain en **train/test split**,
  jamais sur l'echantillon entier ni sur des anecdotes.
- Le fichier de verite (`instagram_groundtruth.csv`) est **agnostique au modele** :
  il ne contient que `handle, name, label, confidence, provenance, rationale`.
  Aucune feature dedans (elles perissent et poussent a l'overfit). Cf. README.
- Prefere le juge LLM aux regex pour tout ce qui est fuzzy ; regles deterministes
  reservees aux 2-3 signaux causaux a haute precision.

## 6. Ta tache : le harness d'eval

Objectif : mesurer si le pipeline classe bien, AVANT de toucher aux regles.

1. **Snapshots** : mecanisme `eval/snapshots/<handle>.json` = sortie brute du
   profile scraper (Apify) figee. Fournir un mode pour (re)peupler les snapshots
   depuis les handles du CSV (fail-soft si pas de token : sauter, ne pas crasher).
   L'eval tourne sur les snapshots, pas sur un scrape live -> reproductible.
2. **Run** : pour chaque handle du groundtruth, passer le(s) etage(s) de
   classification (`judge` / `profile_enrich`, ou la future couche buckets) sur
   son snapshot -> verdict machine.
3. **Mapping** : projeter verdict machine et `label` verite dans le meme espace.
   Cible : seul `opening` doit tomber en `a_contacter`. Tout autre label y
   arrivant = faux positif.
4. **Metriques** (les sortir clairement) :
   - **precision du bucket `a_contacter`** = LA metrique principale ;
   - rappel des `opening` ;
   - matrice de confusion par label ;
   - option : exclure `confidence=low` du calcul strict.
5. **CLI** : ex. `python -m app.ingestion.eval.run` -> imprime un rapport lisible
   (+ JSON optionnel). Ne fait AUCun tuning automatique de seuil.
6. **Tests** : un test qui verifie le calcul des metriques sur un mini-jeu jouet
   (pas les vrais comptes) pour ne pas dependre du reseau.

Ne PAS, dans cette tache : changer les seuils du pipeline, ni ajouter les regles
buckets. D'abord mesurer. Les regles viendront apres, validees par ce harness.

## 7. Ordre des chantiers ensuite (apres l'eval)

- P0 : canal (cabler `has_social_presence = bool(instagram)`) + ville fiable.
- P1 : de-hardcoder l'IdF (France entiere) ; couche deterministe buckets
  (deja-ouvert / chaine / fast-food) reglee via l'eval.
- P2 : `judge()` en unitaire ; logging/observabilite.

## 8. Limites honnetes du jeu de verite (a garder en tete)

~20 lignes = v1 indicatif, biais de collecte (sur-representation des ouvertures
cherchees, manque un tirage aleatoire d'etablis), annotateur unique non valide.
Le premier vrai livrable apres le harness = **agrandir a ~100-150 comptes** avec
un echantillon aleatoire, sinon la precision mesuree reste fragile.
