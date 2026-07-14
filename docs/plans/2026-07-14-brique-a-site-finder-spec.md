# Brique A — Moteur de découverte de site (site_finder) — SPEC

Préparée le 2026-07-14 pour le « chantier fiches » (100 % gratuit, PAS lancé).
Brief source : `docs/plans/2026-07-14-chantier-fiches-gratuit.md`.

Entrée : une fiche `opportunities` (nom d'établissement + ville + adresse +
dirigeants + SIREN/SIRET). Sortie : l'URL du site **PROPRE** du lead, ou **rien**.

Doctrine **VIDE > FAUX** : mieux vaut aucun site attribué qu'un site d'un autre
commerce. Le verrou d'identité ne s'affaiblit JAMAIS (pas de seuil abaissé, pas
de cas exclu). Scraping poli (throttle >= 2,5 s, User-Agent existant de
`website_scraper`, aucun contournement anti-bot). Aucune écriture dans
`chr_signal_radar.db` par ce chantier (le CLI aura `--apply` mais on ne
l'exécute pas ; tests sur engine SQLite temporaire uniquement).

Conventions repo respectées : Python 3.9 (`Optional[X]`, jamais `X | None`),
docstrings en **français**, tests **sans réseau** (fixtures HTML, fetch
injecté), suite complète verte (571+).

---

## 1. Module `backend/app/ingestion/enrichment/site_finder.py`

### 1.1 Rôle et frontière

`site_finder` NE scrape PAS de contacts. Il répond à une seule question :
« quel est le site propre de CETTE fiche ? ». Une fois le site trouvé et écrit
dans `website` (par la Brique B via `find_sites --apply`), les passes existantes
`enrich_phones` / `enrich_site_contacts` prennent le relais **inchangées**.

### 1.2 Réseau : recherche web gratuite et polie

Endpoint HTML de DuckDuckGo, sans clé : `https://html.duckduckgo.com/html/?q=<query>`.
Choisi car il rend du HTML statique parsable, sans JS, sans anti-bot dur (à la
différence de Pages Jaunes/DataDome et Houzz, exclus par doctrine).

- **Throttle** : gate module-level `_MIN_INTERVAL = 2.5` s (même patron que
  `annuaires/http.py` : `_last_call = [0.0]`, `time.monotonic()`), appliqué à
  CHAQUE requête réseau — recherche DDG **et** fetch des pages candidates.
- **User-Agent** : `website_scraper.HEADERS` (`"Mozilla/5.0 (compatible;
  CHR-Signal-Radar/0.1)"`) — imposé par le brief (le UA de `annuaires/http.py`
  est réservé aux connecteurs d'annuaire).
- **Fail-soft** : toute erreur réseau / statut != 200 / MIME non-HTML → `None`,
  jamais d'exception qui remonte. Cap de lecture `website_scraper._PAGE_CAP`.
- **Injection pour les tests** : la fonction publique prend un paramètre
  `fetch: HtmlFetch = _polite_get` (type `Callable[[str], Optional[str]]`,
  réutilisé/ré-importé du patron `annuaires.http.HtmlFetch`). Les tests passent
  un faux `fetch` alimenté par les fixtures → **zéro réseau**. `_polite_get` est
  le seul point qui touche le réseau réellement.

**Séquence de requêtes DDG** (repli, s'arrête dès qu'un candidat passe le
verrou) :
1. `"<nom> <ville>"`
2. repli : `"<nom> architecte intérieur <ville>"`
3. repli : `"<dirigeant> architecte intérieur <ville>"` (nom+prénom du 1er
   dirigeant si présent).

Le nom injecté dans la requête est le nom **brut** de la fiche (la requête sert
à ratisser ; le filtrage strict se fait au verrou). La ville est celle de la
fiche ; si vide, on tente le CP extrait de l'adresse.

### 1.3 Parsing des résultats DDG

Fonction pure `parse_ddg_results(html: str) -> List[str]` (testable sans
réseau) :
- extrait les ancres de résultats : `class="result__a"` (`href="..."`).
- DDG HTML enveloppe la cible dans une redirection
  `//duckduckgo.com/l/?uddg=<url-encodée>&rut=...` → décoder le paramètre `uddg`
  via `urllib.parse.parse_qs` + `unquote`. Si l'href est déjà une URL directe,
  la garder telle quelle.
- dédup par domaine enregistrable, ordre d'apparition conservé, borne à N
  premiers (ex. 8) pour limiter les fetch.

### 1.4 Candidats : domaines PROPRES uniquement

Chaque URL de résultat passe par `enrichment.own_site.own_site` (qui réutilise
`is_real_website`) → écarte plateformes / réseaux sociaux / agrégateurs /
annuaires (`instagram`, `facebook`, `linktr.ee`, `tiktok`, `linkedin`, `houzz`,
`pinterest`, `youtube`, `x.com`, raccourcisseurs, `goo.gl`…). Ne restent que des
domaines susceptibles d'être le site propre du lead. Dédup par domaine
enregistrable (sans `www.`, patron `enrich_phones._site_domain`).

### 1.5 Normalisation du nom — `normalize_name(raw: str) -> str` (pure)

- minuscules ;
- accents retirés (`unicodedata.NFKD` + filtrage des diacritiques) ;
- formes juridiques retirées en tokens entiers (word-boundary), casse/accents
  déjà normalisés : `SARL, SAS, SASU, EURL, SA, SELARL, SELAS, STE, SOCIETE,
  SCI, SNC, SCP, EIRL, EI, SC` — liste `_LEGAL_FORMS` documentée ;
- ponctuation → espaces ; espaces multiples repliés.

`significant_tokens(name: str) -> List[str]` (pure) : tokens normalisés de
longueur >= 3, moins une stoplist de mots génériques du métier
(`_GENERIC_TOKENS` : `studio, agence, atelier, architecture, architecte,
interieur, interieurs, design, decoration, deco, and, the, paris`… — documentée)
pour éviter qu'un site matche sur « agence » seul. Si le nom entier EST
générique (tokens tous filtrés), on retombe sur les tokens >= 3 bruts (ne jamais
rendre une liste vide → sinon le verrou A serait trivialement vrai).

### 1.6 VERROU D'IDENTITÉ STRICT — conditions **A ET B** obligatoires

Avant d'attribuer un site, on fetch la home du candidat (via
`website_scraper._fetch_home` → `(html, url_qui_a_répondu)`, gère variantes
http/https/www) puis, pour la corroboration, les pages contact/mentions
(`website_scraper.contact_page_urls` + `_fetch_html`, cap de pages, throttlé).

**Condition A — le nom matche le site** (au moins une des deux) :
- **A1 (contenu)** : TOUS les `significant_tokens(nom)` présents dans le texte
  agrégé de `<title>` + `<h1>` + `og:site_name` de la home (extraction par regex
  pure `extract_identity_markers(html)`), comparaison sur texte normalisé
  (`normalize_name`) ;
- **A2 (domaine)** : le cœur du domaine (sans TLD ni `www.`) ~ nom normalisé,
  distance tolérante : `difflib.SequenceMatcher(...).ratio() >= 0.85` entre le
  cœur de domaine et la concaténation des tokens significatifs, OU les tokens
  significatifs forment une sous-séquence contiguë du cœur de domaine (ex.
  `atelierdupont.fr` ~ « Atelier Dupont »). `difflib` = stdlib, pur, testable.

**Condition B — >= 1 corroboration INDÉPENDANTE** trouvée sur home + contact +
mentions légales (texte agrégé, normalisé pour ville/nom ; chiffres bruts pour
SIREN/SIRET) :
- **B-ville** : ville de la fiche présente (normalisée) ; OU
- **B-cp** : code postal (5 chiffres extraits de `address`, garde de frontière
  `\b\d{5}\b`) présent ; OU
- **B-dirigeant** : nom de famille d'un dirigeant présent (normalisé, token
  entier, longueur >= 3 pour éviter les faux positifs sur un patronyme court) ;
  OU
- **B-siren/siret** : `siren` (9 chiffres) ou `siret` (14 chiffres) présents
  dans le texte (mentions légales) — matching sur chiffres bruts après
  suppression des séparateurs, gardes de frontière.

B est **indépendante de A** par construction (A porte sur le nom, B sur
géo/dirigeant/immatriculation), donc « A ET B » = deux axes de preuve distincts.

**Décision** : site attribué **seulement si A ET B**. Sinon → **AUCUN site**
(VIDE > FAUX). Le premier candidat qui passe A ET B gagne ; on n'attribue jamais
deux sites.

### 1.7 Traçabilité (audit)

`@dataclass SiteFindResult` (Python 3.9, `Optional`) documentant CHAQUE signal :
```
opp_id: int
name_raw: str
queries: List[str]                 # requêtes DDG réellement émises
candidates: List[str]              # domaines propres retenus (post own_site)
website: Optional[str]             # site attribué, ou None
verdict: str                       # "found" | "locked_out" | "no_candidate" | "error"
name_signal: Optional[str]         # "A1_content" | "A2_domain" | None
corroboration: List[str]           # sous-ensemble de ["ville","cp","dirigeant","siren","siret"]
inspected: List[Dict]              # par candidat : {domain, a_pass, b_signals, title}
from_cache: bool
```
- **`found`** : A ET B passés, `website` renseigné.
- **`locked_out`** : au moins un candidat propre inspecté mais AUCUN n'a passé
  A ET B (inclut l'homonyme : le refus attendu). `website=None`.
- **`no_candidate`** : DDG n'a rendu aucun domaine propre (que des plateformes,
  ou rien). `website=None`.
- **`error`** : exception inattendue (fail-soft, comptée à part). `website=None`.

Fonction publique principale :
```
def find_site(opp, session, fetch=_polite_get, today=None) -> SiteFindResult
```
`opp` peut être un `Opportunity` ou un petit dataclass équivalent (les tests
n'ont pas besoin de la vraie table pour la logique pure). La logique de décision
est isolée dans des helpers PURS (parsing DDG, verrou A, verrou B, normalisation)
testables sans réseau ni base.

### 1.8 Cache — réutilise `verdict_cache` (table `handle_verdicts`), clés `sitefind:`

Aucune nouvelle table, aucune migration. On réutilise `verdict_cache.get` /
`upsert` / `should_rejudge` avec des clés **préfixées** (même mécanisme que
`arch:` pour la population architectes) → jamais de collision avec les verdicts
CHR/`arch:`. Les verdicts `sitefind:*` ne sont dans aucune fenêtre de
`REVISIT_MONTHS` → `revisit_after` = +2 mois par défaut (repêchage périodique
raisonnable), et jamais dans `NEVER_CACHED`.

Deux familles de clés :
- **Cache de RECHERCHE** (jamais deux fois la même recherche réseau) :
  `handle = "sitefind:q:" + sha1(normalize_query(query))`. On stocke la liste des
  URLs résultats DDG **sérialisée en JSON** dans le champ `confidence`
  (`verdict="search"`, `profile={}` → `profile_hash` d'empreinte vide). Avant
  tout appel DDG : `verdict_cache.get(session, key)` ; si présent, `json.loads`
  du `confidence` → on NE refait PAS la requête réseau. Sinon, requête + `upsert`.
- **Cache de VERDICT par fiche** : `handle = "sitefind:opp:" + str(opp.id)`
  (repli `"sitefind:siren:" + siren` si `id` absent). `verdict` = le code
  (`found`/`locked_out`/`no_candidate`), `confidence` = URL trouvée ou `None`.
  Un run repris saute les fiches déjà tranchées via `should_rejudge` (utile pour
  ne pas re-chercher les `locked_out`/`no_candidate` — les `found` ne sont de
  toute façon plus ciblées puisqu'elles ont un `website`).

Justification de l'encodage JSON-dans-`confidence` : `verdict_cache` n'expose pas
de champ payload libre, mais `confidence` et `verdict` sont des colonnes `str`
sans contrainte de longueur (SQLite TEXT). On reste ainsi sur la table existante,
sans la modifier, comme mandaté par le brief (« cache de requêtes préfixé
`sitefind:` dans le cache verdicts »). Encodage documenté dans le module.

---

## 2. CLI `backend/app/ingestion/find_sites.py`

Patron calqué sur `enrich_phones.py` (cibles, commit par fiche, stats, `main()`
argparse), doctrine identique.

```
python -m app.ingestion.find_sites --population architecte --source sirene_stock \
    --limit N [--dry-run | --apply]
```

- **Cibles** : fiches SANS site — `Opportunity.website.is_(None)` OU
  `Opportunity.website == ""`, filtrées par `--population` et (optionnel)
  `--source`. Extrait PUR `_site_targets(session, population, source, limit)`
  (testable sans réseau, patron `enrich_phones._phone_targets`). Défaut
  `--population architecte --source sirene_stock` (les 2 987 fiches stock sans
  tél/sans site du constat).
- **`--dry-run` (défaut, non destructif)** : pour chaque fiche, `find_site` →
  écrit une ligne **JSONL** sur stdout (ou `--out <path>`) :
  `{opp_id, establishment_name, city, queries, candidates, website, verdict,
  name_signal, corroboration, inspected}`. NE TOUCHE PAS la base (le cache
  `sitefind:` peut être écrit si une `session` est fournie, mais aucune écriture
  dans `opportunities`).
- **`--apply`** : si `verdict == "found"`, écrit `opp.website = result.website`
  **UNIQUEMENT si le champ était vide** (jamais d'écrasement), puis
  `session.add(opp); session.commit()` — **commit PAR FICHE** (reprenable,
  fail-soft `try/except` + `rollback`, patron `run_phone_enrich`). Aucune autre
  colonne modifiée (la Brique B lancera ensuite `enrich_phones`).
- **Un seul écrivain SQLite** : rappel en aide/CLI — ne jamais tourner en même
  temps qu'une autre passe d'enrichissement (B/C).
- `--dry-run` et `--apply` mutuellement exclusifs ; `--dry-run` par défaut (sûr).

`@dataclass SiteStats` (patron `PhoneStats`) : `population`, `source`, `scanned`,
`found`, `locked_out`, `no_candidate`, `errors`. Affichage final via `asdict`
(prints ASCII-safe, cp1252 Windows ; `PYTHONIOENCODING=utf-8`).

```
def run_find_sites(population, source, limit, apply=False, out=None,
                   session=None, fetch=None) -> SiteStats
```
Fetch injectable pour les tests ; en prod, `fetch=None` → `site_finder._polite_get`.

---

## 3. Tests sans réseau — `backend/tests/test_site_finder.py` + `test_find_sites.py`

Fixtures HTML sous `backend/tests/fixtures/site_finder/` (aucune requête
réseau ; `fetch` factice = dict `{url: html}` alimenté par ces fixtures, patron
des tests de connecteurs d'annuaire).

Fixtures :
- `ddg_results.html` : page de résultats DDG simulée (ancres `result__a` avec
  redirections `uddg=`), contenant le bon candidat + 1 plateforme
  (`instagram.com/...`) + 1 homonyme.
- `site_match.html` : site du bon lead — `<title>` avec le nom, ville + CP +
  nom du dirigeant + SIREN en mentions légales (A1/A2 + B multi-signaux).
- `site_homonym_othercity.html` : MÊME NOM, AUTRE ville — title matche (A OK)
  mais ni ville/CP/dirigeant/SIREN de la fiche (B échoue).
- `site_homonym_samecp.html` : NOM DIFFÉRENT, même CP que la fiche — CP présent
  (B pourrait passer) mais le nom ne matche pas (A échoue).
- `site_platform.html` : non utilisé pour fetch (exclu en amont par `own_site`).

Cas de test :
1. **Nominal** : DDG rend le bon domaine → home + mentions corroborent →
   `verdict="found"`, `website` = le bon domaine, `name_signal` renseigné,
   `corroboration` non vide.
2. **Homonyme MÊME NOM autre ville → REFUS** : A passe, B échoue →
   `verdict="locked_out"`, `website is None` (le cœur du VIDE > FAUX).
3. **Homonyme même CP mais nom différent → REFUS** : B-cp passerait, A échoue →
   `verdict="locked_out"`, `website is None`.
4. **Plateformes exclues** : DDG ne rend que `instagram.com`/`houzz` →
   `own_site` filtre tout → `verdict="no_candidate"`, aucun fetch de candidat.
5. **Cache hit** : deux appels `find_site` pour la même requête → le `fetch`
   factice n'est appelé qu'UNE fois pour la recherche DDG (compteur d'appels
   vérifié) ; le 2e lit `sitefind:q:*` en base (engine SQLite temporaire).
6. **Purs** : `normalize_name` (formes juridiques/accents/ponctuation),
   `significant_tokens` (stoplist + repli nom générique), `parse_ddg_results`
   (décodage `uddg`, dédup), verrou A (A1 contenu / A2 domaine distance),
   verrou B (chaque signal isolément), `extract_identity_markers`.
7. **CLI `test_find_sites.py`** : `_site_targets` ne rend que les fiches sans
   site de la bonne population/source (engine SQLite temporaire, patron
   `test_enrich_phones`/`test_run_prescripteurs_cli`) ; `--dry-run` n'écrit RIEN
   dans `opportunities` et produit du JSONL parsable ; `run_find_sites(apply=True)`
   avec `fetch` factice écrit `website` seulement sur les `found` et ne touche
   pas une fiche déjà pourvue ; stats cohérentes
   (`scanned==found+locked_out+no_candidate+errors`).

Gate qualité (hors périmètre de cette spec de code, rappel du brief) :
échantillon GT N=50 fiches stock annotées à la main, **0 site attribué à tort**,
rendement mesuré (~40–60 % attendu). Réutilisable via un futur
`eval/site_finder_gt_sample.py` (non couvert ici).

---

## 4. Fichiers à créer / modifier

**Créer :**
- `backend/app/ingestion/enrichment/site_finder.py` (moteur + verrou + cache).
- `backend/app/ingestion/find_sites.py` (CLI dry-run/apply).
- `backend/tests/test_site_finder.py`.
- `backend/tests/test_find_sites.py`.
- `backend/tests/fixtures/site_finder/ddg_results.html`
- `backend/tests/fixtures/site_finder/site_match.html`
- `backend/tests/fixtures/site_finder/site_homonym_othercity.html`
- `backend/tests/fixtures/site_finder/site_homonym_samecp.html`
- `backend/tests/fixtures/site_finder/site_platform.html`

**Réutiliser SANS modifier** (imports uniquement) :
- `enrichment/website_scraper.py` : `HEADERS`, `_PAGE_CAP`, `_fetch_home`,
  `_fetch_html`, `contact_page_urls`.
- `enrichment/own_site.py` : `own_site` (+ `url_filter.is_real_website` indirect).
- `ingestion/verdict_cache.py` : `get` / `upsert` / `should_rejudge` (clés
  `sitefind:`).
- `ingestion/annuaires/http.py` : type `HtmlFetch` (patron d'injection).
- `models.py` : `Opportunity`, `HandleVerdict` ; `database.engine` / `init_db`.

**Aucune modification** de `models.py`, `verdict_cache.py`, `website_scraper.py`,
`own_site.py` n'est requise (additions pures) → aucun risque pour les gates
existants ni la suite de 571+ tests. **Ne pas committer** (l'orchestrateur
committe après revue) ; **ne pas exécuter `--apply`** sur la vraie base.

> Note : la clause « aucune modification de `own_site.py` » est LEVÉE par les
> durcissements ci-dessous — la blocklist d'agrégateurs devient PARTAGÉE dans
> `own_site` (voir §6.1). Additions rétro-compatibles, suite complète verte.

---

## 6. Durcissements post-gate du 2026-07-14

Le gate GT réel (15 fiches annotées à la main) a révélé **6 attributions
fausses** — le verrou d'identité était contournable par trois familles de faux
positifs. Correctifs (tous en TDD, un test adverse par cas réel dans
`tests/test_site_finder_adverse.py`, section « DURCISSEMENTS ») :

### 6.1 Blocklist d'agrégateurs/annuaire ÉTENDUE et PARTAGÉE

Des agrégateurs SIRENE étaient acceptés comme sites propres
(`118000.fr/e_C0101327518`, `le-site-de.com/…_33582.html`,
`prosmaison.fr/entreprise-43435829700076`, `hexagone-architecture.fr` — annuaire
de devis). Ils republient nom/ville/CP/SIREN de TOUTE entreprise → la
corroboration B y passe TRIVIALEMENT à tort.

- La blocklist quitte `site_finder` (constante locale `_DIRECTORY_HOSTS`
  supprimée) et devient **partagée** dans `own_site.py` :
  - `DIRECTORY_HOSTS` (sous-chaînes d'hôte) — étendue à `118000.fr`, `118712.fr`,
    `le-site-de.com`, `prosmaison.fr`, `hexagone-architecture.fr`, `hoodspot`,
    `mappy.com/.fr`, en plus des `pappers`/`societe.com`/`verif`/`infogreffe`/
    `annuaire-entreprises`/`kompass`/`pagesjaunes`… déjà présents ;
  - `DIRECTORY_URL_RE` (motifs d'URL de FICHE GÉNÉRÉE, indépendants de l'hôte) :
    `/entreprise-\d{9,14}`, `e_C\d+`, `_\d{4,}\.html`.
- `own_site()` rejette désormais ces hôtes ET ces motifs → bénéfice PARTAGÉ par
  `enrich_phones` / `enrich_site_contacts`. `site_finder` importe
  `own_site.is_directory` (défense de profondeur, trace explicite).

### 6.2 Signal de NOM validé sur la HOME DU DOMAINE RACINE

`DAMSO`/agrégateurs matchaient le nom sur la **page profonde** renvoyée par DDG.
Or `home_url_variants` conserve le chemin profond → A était vérifié sur une fiche
riche. Désormais `_inspect_candidate` reconstruit `https://<domaine>/` et fetch
la HOME RACINE ; A (title/h1/og:site_name) est validé UNIQUEMENT dessus. Un
agrégateur a une home générique (« Trouvez un pro… ») qui ne matche jamais le nom
du studio.

### 6.3 « ville » seule n'est PLUS une corroboration suffisante

`DAMSO INTERIEURS` (Lyon) a matché la billetterie d'un concert de Damso à Lyon
(nom OK + ville OK, rien d'autre). Nouveau gate `_corroboration_ok(signals)` : B
suffit si elle contient un signal FORT (`cp`/`dirigeant`/`siren`/`siret`) OU
≥ 2 signaux distincts (`ville` + un second). `ville` seule → REFUS.

### 6.4 Similarité domaine~nom resserrée (tokens complets, pas sous-chaîne)

`ARCHIVEST` matchait `archives.territoiredebelfort.fr` (`archivest` est une
sous-chaîne de `archives…t`). `_domain_matches_name` n'utilise plus la
sous-chaîne contiguë : match par ratio `difflib >= 0.85` sur la concaténation,
OU chaque token significatif est un SEGMENT COMPLET du domaine (découpé sur
tirets/points/underscores via `_domain_segments`).

### 6.5 Attribution UNIQUEMENT si la home racine a répondu

`_inspect_candidate` expose `home_alive` ; `find_site` n'attribue que si
`home_alive and a_pass and _corroboration_ok(b_signals)`. Un domaine mort
(timeout/404/MIME) ne peut plus être attribué même si son nom « colle ».

---

## 7. Calibrage du 2026-07-14

Le durcissement post-gate (§6) avait supprimé les 6 faux positifs, mais un
**second gate GT** (échantillon de fiches stock annotées) a montré qu'il était
allé **trop loin sur un cas** et laissait par ailleurs deux marges d'exclusion
excessives. Objectif du calibrage : **retrouver les vrais positifs de référence
SANS réintroduire les faux de référence** (agrégateurs 118000/le-site-de/
prosmaison, billetterie concert Damso, archives de Belfort). Tout en TDD (chaque
vrai positif perdu = un test rouge-avant/vert-après ; les tests adverses des faux
restent verts). Aucune des gardes INTERDITES d'affaiblissement n'est touchée
(blocklist agrégateurs, rejet « ville seule », domaine mort).

### 7.1 Diagnostic des vrais positifs de référence

- **Vrais sites propres à retrouver** : `emdecoration.fr` (fiche 593),
  `pkinterieur.com` (1554), `catherinelassalle.fr` (1518).
- **`emdecoration.fr` / `pkinterieur.com`** : perte **AMONT DDG** (rejeu réseau
  réel → HTTP 202 + page anti-bot « anomaly » → 0 résultat parsable → `no_candidate`).
  Le verrou N'EST PAS en cause : dès que DDG répond, le nom concorde (A1/A2) et un
  signal fort corrobore → TROUVÉ. Ajoutés en **fixtures de non-régression**
  (`site_emdecoration.html`, `site_pkinterieur.html`) pour garantir qu'un futur
  durcissement ne tue pas un vrai site au nom concordant. **Non corrigeables en
  code** (blocage réseau, hors périmètre du verrou).
- **`catherinelassalle.fr`** : VRAI faux négatif de **sur-durcissement**. Raison
  sociale abrégée/fusionnée « CAT LASSALLE » (`significant_tokens` →
  `['cat','lassalle']`) : A1 échoue (la home dit « Catherine », pas « cat »),
  A2 échoue (`difflib('catherinelassalle','catlassalle') < 0.85` et le label de
  domaine est unique → `cat`/`lassalle` ne sont pas des SEGMENTS). Rejeté MÊME si
  DDG répondait. **Corrigé** par la VOIE C ci-dessous.
- **Pages d'annuaire comptées « TP » par erreur au 1er gate** (582 `118000.fr`,
  1501/1497/1514/1515 `le-site-de`, 1551/1567 `prosmaison`, 1547 `homestagingki`,
  1563 `maison.fr`) : ce ne sont PAS des sites propres → **volontairement NON
  retrouvées** (doctrine VIDE > FAUX ; ces hôtes/motifs restent bloqués).

### 7.2 VOIE C — identité par le nom COMPLET du dirigeant

Nouveau chemin d'identité, **alternatif au verrou A**, pour les raisons sociales
qui ne matchent NI le contenu NI le domaine (nom abrégé/fusionné). Identité
valide, même sans A, si sur le texte agrégé (home racine + mentions/contact) :

- le **nom COMPLET du dirigeant** (prénom ET nom, ≥ 2 tokens de ≥ 3 caractères,
  `_dirigeant_identity_tokens`) est présent — le patronyme SEUL ne suffit pas
  (déjà couvert par le signal B « dirigeant », trop peu discriminant) ; **ET**
- un **signal FORT géo/immatriculation** figure aussi (`cp` **OU** `siren` **OU**
  `siret` — `_STRONG_GEO_IMMAT`). « ville » seule ne déclenche JAMAIS la voie C.

Deux ancres indépendantes et hautement discriminantes (un prénom + nom complets
coïncidant AVEC un CP/SIREN par pur hasard est improbable) → sûr au regard de
VIDE > FAUX. `_check_lock_c(opp, aggregated_text, b_signals) -> bool`, exposé par
`_inspect_candidate` (`c_pass`). Décision `find_site` :
`home_alive AND ((a_pass AND _corroboration_ok(b)) OR c_pass)`. `name_signal`
vaut `"C_dirigeant"` quand seule la voie C tranche.

La voie C **ne rouvre aucune porte** : les agrégateurs sont écartés en amont
(`own_site`/`is_directory`) et leur home racine générique ne cite aucun dirigeant
nommé ; Damso (billetterie) et les archives de Belfort ne citent pas le nom
complet du dirigeant de la fiche (tests adverses 14–15).

### 7.3 `DIRECTORY_URL_RE` rééquilibrée (moins d'exclusions à tort)

Deux motifs étaient trop larges et excluaient des pages légitimes :

- `e_C\d+` → `/e_C\d+` : ancré en début de SEGMENT de chemin (l'id de fiche
  118000 est `/e_C<id>`) — plus de `…e_C…` fortuit au milieu d'un mot ;
- `_\d{4,}\.html` → `_\d{5,}\.html` : les identifiants le-site-de font ≥ 5
  chiffres (`_251396`, `_33582`, `_46787`) ; un **slug daté légitime**
  `…_2024.html` (année, 4 chiffres) n'est plus pris pour une fiche d'annuaire.

Les fiches d'agrégateur réelles restent détectées (test 16) ; les hôtes de
`DIRECTORY_HOSTS` restent bloqués inconditionnellement.

### 7.4 Pistes ÉCARTÉES (jugement)

- **Tester la page profonde DDG quand elle appartient au même domaine propre**
  (au lieu de la seule home racine) : **écarté**. C'est exactement le vecteur des
  agrégateurs neutralisé en §6.2 (home racine générique, fiche profonde riche) ;
  le rouvrir affaiblirait une garde INTERDITE, et **aucun** des 3 vrais sites
  propres de référence n'en a besoin (593/1554 matchent leur home ; 1518 passe
  par la voie C). Le test adverse 12 (`agg_home_generic`) reste vert.

### 7.5 Tests (rouge-avant / vert-après)

- `test_reference_fiche1518_catherinelassalle_recovered_via_dirigeant`
  (`test_site_finder.py`) : ROUGE avant (locked_out, `a_pass=False`), VERT après
  (found, `c_pass=True`, `name_signal="C_dirigeant"`).
- `test_reference_fiche593_emdecoration_found_via_name`,
  `test_reference_fiche1554_pkinterieur_found_via_name` : non-régression (vrais
  sites propres au nom concordant, trouvés dès que DDG répond).
- Purs voie C : `test_dirigeant_identity_tokens_requires_two_tokens`,
  `test_lock_c_full_name_plus_strong_signal_passes`,
  `test_lock_c_requires_strong_geo_immat_not_ville`,
  `test_lock_c_requires_full_name_present`.
- Adverses (`test_site_finder_adverse.py`, section « CALIBRAGE ») :
  `test_lock_c_full_name_without_strong_signal_is_refused`,
  `test_find_site_dirigeant_name_but_only_ville_is_refused`,
  `test_lock_c_does_not_resurrect_damso_or_archives`,
  `test_directory_url_re_still_flags_aggregator_fiches`,
  `test_directory_url_re_spares_legit_dated_pages`.

Suite complète : **680 tests, 100 % verts.**

---

## 8. Couche de recherche — fiabilisation du 2026-07-14

### 8.1 Diagnostic

Le verrou d'identité est bon (gate « 0 faux positif » tenu), mais les vrais
sites étaient perdus **EN AMONT du verrou** : l'endpoint HTML de DuckDuckGo
répond par des **défis anti-bot (HTTP 202)** même à 2,5 s d'intervalle. Sur le
constat GT, ~33 `no_candidate` sur 40 étaient en réalité des **recherches jamais
servies** (moteur muet), pas de vraies absences de site. Un `no_candidate` mis en
cache fige alors la fiche pour +2 mois alors qu'elle est RÉESSAYABLE. Aucun
contournement anti-bot n'est autorisé (pas de rotation d'UA/proxy, pas de
spoofing) — uniquement de la **politesse** : cadence lente, retry avec backoff,
repli sur un autre moteur public.

### 8.2 Cadence dédiée aux recherches

Deux gates de cadence DISTINCTS (`site_finder`) :

- **Pages candidates** : `_MIN_INTERVAL = 2,5` s (`_throttle`), inchangé.
- **Recherches moteur** : `_SEARCH_MIN_INTERVAL = 10` s + jitter aléatoire
  `[0, 2]` s (`_search_throttle`, gate `_last_search_call` séparé). Une recherche
  est bien plus « chère » côté anti-bot qu'un fetch de page propre.

Sur un **défi anti-bot** (HTTP 202, ou corps 200 contenant un marqueur
`_CHALLENGE_MARKERS` : `anomaly`, `unusual traffic`, `captcha`…), `_polite_search_get`
fait **UN SEUL retry** après un **backoff LONG (30-60 s)** ; si le défi persiste,
il RENONCE proprement (`None`) — jamais de spam, jamais de contournement.

`_polite_get` reste le **point réseau unique** : il ROUTE les URL de moteur
(`_is_search_url` : hôtes DDG/Bing) vers `_polite_search_get`, et les pages vers
le throttle 2,5 s. Les tests injectent un faux `fetch` et bypassent tout le
réseau ; le retry/backoff est testé unitairement en mockant `requests.get` +
`time.sleep` (aucune attente réelle, zéro réseau).

### 8.3 Moteur de repli — liste ordonnée de moteurs

`_ENGINES` : liste ORDONNÉE de `SearchEngine` (nom + gabarit d'URL + parseur
pur), **DuckDuckGo puis Bing HTML public** (`https://www.bing.com/search?q=…`).
`_run_engines(query, fetch)` interroge les moteurs dans l'ordre et rend le
**PREMIER qui sert au moins un résultat**. Un moteur « ne sert pas » quand
`fetch` rend `None` (202/défi/MIME/réseau) OU que son parseur ne trouve aucun
résultat (corps vide/malformé) → on tente le suivant. Ajouter un moteur = une
ligne (URL + parseur), chacun testable unitairement.

- **Parseur DDG** : `parse_ddg_results` (inchangé, décode `uddg=`).
- **Parseur Bing** : `parse_bing_results` — blocs `<li class="b_algo">` →
  `<h2><a href>`, décodage de la redirection `/ck/a?…&u=a1<base64-url-safe>`
  (`_decode_bing_href`) ou href direct, dédup par domaine, même borne
  `_MAX_DDG_RESULTS`.

### 8.4 Distinction « non servie » vs « aucun candidat »

`_search` rend un `SearchOutcome(urls, served, engine)`. `served=True` ssi un
moteur a réellement répondu des résultats (ou hit de cache). Nouveau verdict de
fiche :

- **`no_candidate`** : au moins un moteur a **SERVI**, mais aucun candidat propre
  (que des plateformes/annuaires, ou 0 résultat exploitable). Signal légitime,
  **cacheable**.
- **`search_unavailable`** (NOUVEAU) : **AUCUN** moteur n'a servi (tous muets :
  202/défi/vide/malformé). **RÉESSAYABLE**, **jamais mis en cache** (comme
  `error`). Crucial pour piloter la brique B : ne PAS marquer définitivement une
  fiche qu'on n'a jamais pu chercher.

Décision `find_site` (après la séquence de requêtes en repli, `website` non
trouvé) : `locked_out` si ≥ 1 candidat propre inspecté et rejeté ; sinon
`no_candidate` si ≥ 1 requête servie ; sinon `search_unavailable`.
`SiteStats.search_unavailable` est compté à part dans le CLI.

### 8.5 Cache : jamais un échec de moteur stocké comme vide

- **Cache de requête** (`sitefind:q:`) : `_search` n'écrit QUE des recherches
  **servies** (`served=True`) — une recherche non servie n'écrit rien (le repli
  Bing muet compris). On ne met donc jamais en cache une liste vide issue d'un
  défi anti-bot.
- **Cache de verdict fiche** : `skip_cache` couvre `error`, `search_unavailable`,
  ET `no_candidate` obtenu alors qu'au moins une requête de la séquence a été
  muette (`any_muted`) — une requête de repli non servie a pu masquer le vrai
  site.

### 8.6 Tests (sans réseau)

- Fixtures : `bing_results.html` (résultats organiques Bing, dont une
  redirection `/ck/a`), `ddg_challenge.html` (page de défi « anomaly »).
- `test_site_finder_search_layer.py` (23 tests) : `parse_bing_results` (ck/a,
  href direct, dédup, cap, vide) ; `_looks_like_challenge` ; `_run_engines`
  (DDG servi → pas de Bing ; DDG muet → repli Bing ; DDG vide → repli ; tous
  muets → non servi) ; cache (servi mis en cache, muet jamais, hit servi) ;
  cadence (`_SEARCH_MIN_INTERVAL ≥ 10`, `_is_search_url`) ; retry/backoff
  (`_polite_search_get` : 202 puis 200 → un retry ; 202×2 → abandon ; corps de
  défi 200 → None) via `requests`/`time.sleep` mockés ; intégration (repli Bing
  sauve un vrai site perdu par un 202 ; `search_unavailable` non caché et
  réessayable ; moteur servi mais plateformes seules → `no_candidate`).
- Tests adverses mis à jour : `test_network_timeout_fail_soft`,
  `test_ddg_empty_or_malformed_no_crash`,
  `test_transient_ddg_failure_is_not_cached_as_no_candidate`,
  `test_opp_all_none_does_not_crash` reflètent désormais `search_unavailable`.

Suite complète : **715 tests, 100 % verts.**
