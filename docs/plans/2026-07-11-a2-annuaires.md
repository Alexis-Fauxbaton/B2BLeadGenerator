# Annuaires + délta jeunes studios (A2) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL — utiliser `superpowers:subagent-driven-development` (recommandé) ou `superpowers:executing-plans` pour exécuter ce plan tâche-par-tâche. Étapes en `- [ ]` pour le suivi. Chaque tâche porte un **Modèle d'exécution recommandé** pour l'orchestrateur.

**Goal:** Livrer LE VOLUME de la population `architecte` (introduite en A1) via deux nouvelles sources de découverte, EN PLUS du funnel Instagram A1 et SANS jamais toucher au CHR ni à ses évals :

1. **Le STOCK qualifié — annuaires professionnels** (`source='annuaire'`) : **CFAI** (738 architectes d'intérieur, ~100 % cibles par définition) et **UFDI** (~157 décorateurs/architectes en un fetch de la page France, avec signal hospitality NATIF). Scraping DOUX (`requests`+`BeautifulSoup`, throttle 2,5 s, User-Agent honnête, robots respecté), 100 % HTML statique confirmé par la sonde — **aucun Playwright**. Membres d'un ordre/fédération = `lifecycle_label='studio_actif'` sans juge LLM (source de confiance).
2. **Le FLUX des jeunes studios — délta Sirene archi** (`source='jeunes_studios'`) : réutilise la brique 2 (`insee.fetch_new_etablissements`) pointée sur **NAF 71.11Z + 74.10Z**, fenêtre de création récente (PAS le stock), + **filtre de qualification mots-clés** sur la dénomination (rendement MESURÉ par la sonde). Un studio qui vient de se créer n'a pas encore de fournisseur attitré = meilleure cible commerciale. Volume borné (limite quotidienne).

**Sirene en ENRICHISSEUR des deux** : les leads annuaire reçoivent leur **SIREN** via un **matcher architecte** (nouveau, gate NAF 71.11Z/74.10Z, `match_architecte`) fondé sur société + ville + domaine du site — ce qui débloque **dirigeant + ancienneté** et la **fusion cross-source par SIREN** avec le délta. Les leads délta portent leur SIREN/dirigeant/ancienneté **nativement** (données INSEE). La déduplication annuaire×Instagram (dont les studios Insta n'ont PAS de SIREN, matcher A1 CHR-gated) repose sur une **fusion douce nom+ville** (conservatrice : vide/2 fiches > faux merge).

**Hypothèse produit validée (Alexis, Ambient Home — luminaires, cible architectes d'intérieur, VOLUME MAX) :** la base A1 (~44 studios Insta, précision 90 %) est trop mince. Les annuaires apportent un stock large et pré-qualifié (CFAI = 100 % de la cible par construction ; UFDI expose « Décoration Hôtels/Restaurants » directement sur chaque fiche → tier T2 immédiat). Le délta INSEE capte les créations très récentes avant même une présence Instagram/annuaire.

**Fondé sur la sonde** (`.superpowers/sdd/sonde-a2.json` + HTML bruts dans `.superpowers/sdd/sonde-a2/`). Décisions tranchées par les sondes ci-dessous.

**Architecture (delta vs A1, tout ADDITIF) :**
- **Connecteurs annuaire** (`CfaiConnector`, `UfdiConnector`) : implémentent l'interface `Connector` (`fetch`/`to_candidates`), fonctions de parsing **PURES** (fixtures = extraits des HTML sondés, aucun réseau en test), HTTP injectable (`HtmlFetch`). Émettent `LeadCandidate(population='architecte', source='annuaire', lifecycle_label='studio_actif')`.
- **Connecteur délta** (`JeunesStudiosConnector`) : sibling de `SireneDeltaConnector`, réutilise `insee.fetch_new_etablissements` (throttle 2,1 s intégré), NAF archi, filtre de qualification, mapping natif SIREN/dirigeant/ancienneté. Émet `LeadCandidate(population='architecte', source='jeunes_studios', lifecycle_label='unknown')`.
- **Matcher architecte** (`siret_matcher.match_architecte`) : chemin PARALLÈLE au `match()` CHR (jamais modifié → `match_eval` 8/9 bit-à-bit intact), gate NAF 71.11Z/74.10Z, nom+ville+domaine, arbitre LLM réutilisé.
- **Orchestration annuaire** (`pipeline.run_annuaires`) : miroir de `run_instagram`/`run_prescripteurs` — connecteur → enrichissement SIREN archi → **fusion douce nom+ville** → `_process_candidate` (branche `population='architecte'` de A1, contourne le classifieur CHR).
- **Délta** : passe par `run_ingestion(source='jeunes_studios')` (machinerie window/incremental/backfill existante), les candidats portant déjà leur SIREN natif → `_process_candidate` les persiste sans ré-enrichir.

**Tech Stack:** Python 3.9 (`Optional[X]`/`Dict`/`List`/`Tuple` de `typing`, **jamais** `X | None`), `requests` + `beautifulsoup4` (**ABSENT du `requirements` aujourd'hui — le `requirements.txt` ne contient que `requests==2.32.3` et bs4 n'est pas installé dans `.venv` ; T1 DOIT l'ajouter ET l'installer AVANT que `pipeline.py` n'importe les connecteurs, sinon toute la suite de tests casse à la collecte**), SQLModel/SQLite, OpenAI (arbitre, optionnel, fail-soft), pytest. Docstrings/commentaires/prompts **en français**. Réutilise `Connector`/`LeadCandidate` (`base.py`), `insee.fetch_new_etablissements`, `clean_name`/`_tokens`/`_geo_consistent`/`arbitrate`/`_result` (`siret_matcher.py`), `_process_candidate`/`_merge_corroboration` (`pipeline.py`), la colonne `population` et le routage neutre `prescripteur actif` (A1).

## Global Constraints

- **Python 3.9** ; **fail-soft partout** : pas de réseau/HTML illisible → page sautée, jamais d'exception ; robots.txt d'un annuaire interdisant une page → cette page n'est PAS scrapée (documenté). Pas de clé INSEE → délta `[]`. Pas de clé OpenAI → arbitre `None` (le lead vit sans SIREN).
- **Aucun appel réseau dans les tests unitaires** : tout HTTP est injecté (`HtmlFetch`/`Fetch`/`fetch`) et alimenté par des **fixtures = extraits des HTML sondés** (`.superpowers/sdd/sonde-a2/`). Le seul réseau autorisé est le **run réel borné** et le **gate LLM live** de la T5 (manuels, hors pytest).
- **Scraping POLI** : throttle 2,5 s entre requêtes, `User-Agent` honnête (`"Ambient Home lead research (contact: alexis.fauxbaton@gmail.com)"`), pagination bornée (`max_pages`), volume borné (`limit`). On ne scrape QUE des chemins autorisés par le robots.txt de chaque site (CFAI permissif ; UFDI : `/decorateur/*.html` **Allow**, `/membres.php` **Disallow** → on n'utilise QUE `/decorateur/*`).
- **Répertoires** : `python`/`pytest` depuis `chr-signal-radar/backend` avec `.venv\Scripts\python.exe` ; `git` depuis la racine `chr-signal-radar/` (les chemins `backend/...` des commits sont relatifs à cette racine). Branche **`feature/a2-annuaires`**. **Pas de push, pas de `--no-verify`.**
- `python -m pytest tests/ -q` **vert à la fin de CHAQUE tâche**.
- **ÉVALS EXISTANTES INTACTES (non négociable)** :
  - CHR : `app.ingestion.eval.run` (gates `recall_opening == 1.0`, `hot_precision >= 0.60`) et `app.ingestion.eval.match_eval` (**8/9, 0 faux merge**) NE DOIVENT PAS BOUGER. Le `match()` CHR n'est **jamais** modifié (le matcher archi est un chemin parallèle neuf). Toutes les modifications de `pipeline.py`/`siret_matcher.py` sont **strictement additives**.
  - Prescripteurs A1 : `app.ingestion.eval.prescripteurs_run` (gates `studio_actif_precision >= 0.70`, `0 hors_cible en tiers`) reste vert. `run_prescripteurs` (A1) N'EST PAS modifié (pas de nouvel enrichissement SIREN injecté dedans → A1 bit-à-bit identique).
- **TDD strict** : tests d'abord (RED), puis implémentation (GREEN), puis commit avec le message exact fourni.
- **Créer la branche avant la Task 1** (depuis la racine) :

```bash
git checkout -b feature/a2-annuaires
```

## Décisions tranchées par les sondes (à lire avant de coder)

**Volet annuaires**

1. **CFAI = HTML statique pur, pagination GET `?page=N`.** `curl` brut == navigateur (aucun JS). Liste : `table.table-list > tbody > tr`, 5 `<td>` (CP, Ville, Nom `<b>NOM Prénom</b>`, Société, lien fiche `/annuaire-professionnel/adherent/<id>`), **15 lignes/page, 50 pages, 738 total** (badge `span.badge.bg-secondary` « 738 résultats »). Fiche : `<h1>Prénom NOM</h1>`, `<p class="member-company">`, `<p class="member-activity">`, adresse/téléphone/**email `mailto:`**/site en clair. → `requests`+`BeautifulSoup`, aucun Playwright. **Robots CFAI permissif** (aucun Disallow).
2. **CFAI — filtre qualité `Membre Honoraire`.** La sonde a trouvé un adhérent (`adherent/17`, ARNAUDEAU) marqué `<p class="member-activity-summary">Membre Honoraire du CFAI</p>` = retraité/honorifique, SANS société ni contact → **hors_cible** (4/5 cibles sur l'échantillon, le seul bruit = honoraire). Garde : si `member-activity-summary` contient « honoraire », le lead est **écarté** (pas de fiche créée).
3. **CFAI — le filtre spécialité Hôtellerie/Restauration est côté SERVEUR seulement** (POST `member_directory_listing` + CSRF), invisible sur la fiche, et **LARGE** (339/738 = 46 %, tag auto-déclaré peu discriminant). → **On NE l'utilise PAS** comme filtre principal (pagination GET simple pour tout le stock). Le signal hospitality CFAI est trop bruité pour porter un tier T2. CFAI → **tier T3** par défaut.
4. **UFDI = le plus simple.** WordPress/Divi statique. Découverte par les pages `/decorateur/decorateurs-france-fr.html` (**recensement national, ~157 profils réels en UN SEUL fetch** — vérifié en rejouant `ufdi.parse_list_page` sur `ufdi-france.html` : 157 cartes `div.et_pb_team_member`). ⚠️ **NE PAS confondre avec ~255** : la page contient AUSSI ~98 liens de NAVIGATION départementale (`decorateur-decoratrice-architecte-interieur-<dept>-NN.html`, `ain-01`…`reunion-974`) qui matchent le motif `_PROFILE_RE` mais NE SONT PAS des profils. Le parseur les exclut correctement car il scope sur les cartes `div.et_pb_team_member` (les liens dept-nav sont HORS carte) — 157 + 98 = 255. **Le bon volume produit est ~157.** Si plus de volume est réellement nécessaire, crawler EXPLICITEMENT les ~100 pages de listing départemental (elles ne sont PAS des cartes team_member) ; et **garder le repli régional pour qu'il n'ingère jamais un lien dept-nav comme membre** (le scope `div.et_pb_team_member` de `parse_list_page` assure déjà cette garde). Repli possible : les 15 pages régionales (`/decorateur/decorateurs-region-*.html`). Cartes liste : `div.et_pb_team_member` → `h4 > a[href=fiche]` (nom), `<h5>` (société), `<h6>` (ville). Fiche `/decorateur/<slug>-<id>.html` : **téléphone en clair via `data-numero="..."`** (aucun JS malgré l'UI « cliquer pour afficher »), site via `<a class="site" href>`, réseaux (dont Instagram), et **liste d'activités structurée `<li>Décoration Hôtels</li>` / `<li>Décoration Restaurants</li>`**. **PAS d'email en clair** (contact via popup `contact.php?id=`). **Robots UFDI** : `/membres.php` **Disallow**, `/decorateur/*.html` **Allow** → on n'utilise QUE `/decorateur/*`.
5. **UFDI — signal hospitality NATIF → tier T2 sans juge.** Une fiche portant `<li>Décoration Hôtels</li>` ou `<li>Décoration Restaurants</li>` reçoit le libellé A1 `portfolio hospitality/CHR` (secondary, score-bearing +2) → **tier T2**. Sinon T3. (4/5 de l'échantillon UFDI portaient ce tag ; Benedetti sans tag hospitality → T3, correctement.)
6. **Houzz = REPORTÉ (hors périmètre A2, documenté).** Anti-bot actif non-déterministe (« Client Challenge » déclenché même en navigateur réel), SPA React ~2 Mo/page, ToS incertain (SaaS pro, pas annuaire public). Sonde : « Non recommandé comme source principale… ne pas bâtir de pipeline automatisé dessus sans réévaluation (légale + technique) ». → **AUCUN connecteur Houzz.** Documenté dans `docs/population-architectes-design.md` (report vers un éventuel A2bis manuel/API partenaire).
7. **Membres d'annuaire = confiance par construction → PAS de juge LLM.** CFAI (ordre) et UFDI (fédération) ne référencent que des professionnels du métier → `lifecycle_label='studio_actif'` posé directement. Le seul garde déterministe est l'exclusion CFAI « Membre Honoraire » (#2). Économie totale de tokens sur le stock.

**Volet délta jeunes studios**

8. **Le délta INSEE archi est RECALL-ORIENTÉ mais BRUYANT et PARTIELLEMENT AVEUGLE.** Mesuré sur 30 j France (NAF 71.11Z/74.10Z) : **1625 créations/30 j (~54/j)**, dont **91 % d'entrepreneurs individuels** (le filtre catégorie juridique éliminerait 91 % ET de vrais studios solo → **inutilisable seul**) et **65 % de dénominations MASQUÉES** (`[ND]`, non-diffusion → **structurellement invisibles à tout filtre mots-clés**). → Le délta est un flux à **faible priorité/volume** (`lifecycle_label='unknown'`, tier NON attribué), complément des annuaires, pas source principale.
9. **Filtre mots-clés dénomination — rendement MESURÉ.** Sur les dénominations visibles (35 % du flux) : **28,2 % retenus** (147/522). Rapporté au flux total : **9,8 %** (~5 créations qualifiables/jour sur ~54 brutes). Mots-clés (sonde) : `interieur/intérieur, design, studio, agencement, deco/déco, archi, atelier, concept, home, espace`. Précision à l'œil des passants ~80-85 %. **Faux positifs identifiés** : `DESIGN GRAPHIQUE`/graphisme/web/UX (le NAF 74.10Z couvre le design graphique/produit, pas que l'intérieur) → **garde négatif** (`graphique/graphic/graphisme/web/ux/ui/packaging/motion`) pour trimmer ce bruit adjacent. Une dénomination masquée `[ND]` ou vide → **sautée** (injoignable ET inqualifiable, cohérent avec `map_etablissement` de la brique 2).
10. **Ancienneté/dirigeant NATIFS côté délta.** Le record INSEE porte `dateCreationEtablissement` (→ `activity_start_date`, ancienneté) et `uniteLegale.prenom1/nom` (→ `decision_maker` pour les personnes physiques). Le délta n'a donc **pas besoin du matcher** (SIREN/SIRET/NAF déjà présents) : `_process_candidate` le persiste tel quel.

**Enrichissement / dédup (fondé sur le code lu)**

11. **Le matcher A1 est CHR-gated → inopérant pour les archis.** `pick_by_name`/`pick_by_address`/le pool d'arbitre exigent `classify_naf(naf)` (CHR) ; `near_candidates` filtre `section_activite_principale:"I"` (hébergement-restauration). Un NAF archi n'est jamais accepté. A1 (décision #7) a **explicitement reporté à A2** l'élargissement du gate. → A2 ajoute `match_architecte` (chemin **parallèle**, gate 71.11Z/74.10Z, section « M », **sans étage adresse** car les studios sont souvent des bureaux à domicile → nom+ville+domaine + arbitre suffisent), laissant `match()` CHR **intact**.
12. **Dédup annuaire×Instagram = nom+ville, PAS SIREN.** Les studios Insta A1 n'ont pas de SIREN (matcher A1 CHR-gated → `None`). La fusion par SIREN de `_process_candidate` ne peut donc PAS dédoublonner annuaire×insta. → Fusion **douce nom+ville** (normalisée, exacte) déclenchée **uniquement quand le candidat entrant est `source='annuaire'`** (le nouveau flux se réconcilie contre l'existant Insta ; `run_prescripteurs` A1 reste bit-à-bit identique) : **exactement 1** fiche archi d'une AUTRE source au même nom+ville → merge ; **0 ou ≥2** → pas de merge (create/skip). Gate d'éval dédié : **0 faux merge annuaire×insta**.

**Routage des labels (réutilise A1, aucune nouvelle famille de scoring) :**

| source | lifecycle_label | `main_signal` | `secondary_signals` | tier | juge LLM ? |
|---|---|---|---|---|---|
| `annuaire` (CFAI) | `studio_actif` | `prescripteur actif` *(neutre)* | `annuaire cfai` (+ `portfolio hospitality/CHR` si filtre hôtellerie, non utilisé par défaut) | T3 | non |
| `annuaire` (UFDI, hospitality) | `studio_actif` | `prescripteur actif` *(neutre)* | `annuaire ufdi`, `portfolio hospitality/CHR` | **T2** | non |
| `annuaire` (UFDI, sans) | `studio_actif` | `prescripteur actif` *(neutre)* | `annuaire ufdi` | T3 | non |
| `jeunes_studios` (délta) | `unknown` | `prescripteur actif` *(neutre)* | `jeune studio (création récente)` | — | non |

`prescripteur actif` (A1) = famille de scoring NEUTRE (aucun bonus de nature). `portfolio hospitality/CHR` (A1) = famille +2 (déjà émise par les archis en A1, jamais par le CHR). **Aucun nouveau libellé score-bearing → scores CHR bit-à-bit identiques.**

---

### Task 1: Connecteur CFAI (annuaire, HTML statique, filtre honoraire)

**Modèle d'exécution recommandé : sonnet**

**Files:**
- Create: `backend/app/ingestion/annuaires/__init__.py`
- Create: `backend/app/ingestion/annuaires/http.py` (HTTP poli injectable, partagé CFAI/UFDI)
- Create: `backend/app/ingestion/annuaires/cfai.py`
- Create: `backend/tests/test_cfai_connector.py`
- Modify: `backend/requirements.txt` (ajouter `beautifulsoup4>=4.12` — **absent aujourd'hui**, requirements ne liste que `requests==2.32.3`)

**Interfaces:**
- `annuaires/http.py` :
  - `HtmlFetch = Callable[[str], Optional[str]]` — URL → HTML texte, ou `None` (fail-soft).
  - `polite_get(url: str) -> Optional[str]` : `requests.get` avec throttle 2,5 s (variable module `_last_call`), `User-Agent` honnête, `timeout=30`, fail-soft `None`. **Défaut réseau** ; les tests injectent un faux.
  - `USER_AGENT = "Ambient Home lead research (contact: alexis.fauxbaton@gmail.com)"`.
- `annuaires/cfai.py` — parsing **PUR** + connecteur :
  - `LIST_URL = "https://www.cfai.fr/fr/recherche/annuaire-professionnel"`, `BASE = "https://www.cfai.fr"`.
  - `parse_list_page(html) -> List[Dict[str, str]]` : lignes `table.table-list > tbody > tr` → `[{fiche_id, cp, ville, nom, societe, fiche_url}]`. Ignore les `<tr>` sans lien fiche.
  - `parse_total(html) -> Optional[int]` : entier du badge « N résultats », ou `None`.
  - `parse_fiche(html, fiche_id) -> Optional[Dict[str, Any]]` : `{name, company, activity, address, city, phone, email, website, is_honoraire}`. Renvoie `None` si `is_honoraire` (garde #2) ou si `<h1>` absent.
  - `CfaiConnector(Connector)` : `name = "cfai"`. `fetch(since_days, limit, max_pages, **_)` : pagine `?page=1..max_pages` (throttle via `http_fetch`), collecte les lignes liste, puis fetch chaque fiche (borné par `limit`) ; renvoie `List[Dict]` (dicts fiche parsés, honoraires exclus). Pose `self.last_total_count`. HTTP injectable (`http_fetch: HtmlFetch = polite_get`). `to_candidates(records) -> List[LeadCandidate]` : mappe chaque fiche → `LeadCandidate(source="annuaire", source_ref=f"cfai:{id}", population="architecte", lifecycle_label="studio_actif", main_signal="prescripteur actif", establishment_type="architecte d'intérieur", secondary_signals=["annuaire cfai"], establishment_name=company or name, decision_maker=name, city, address, email, website, proof_text, proof_url=fiche_url)`.
- `establishment_name` = société si présente, sinon nom de la personne. `decision_maker` = nom de la personne (dirigeant). Le SIREN/dirigeant/ancienneté viennent de l'enrichissement archi (T4), pas du connecteur.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_cfai_connector.py
"""Connecteur CFAI (A2, T1) — parsing PUR sur extraits des HTML sondés
(.superpowers/sdd/sonde-a2/cfai-*.html). Aucun réseau : http_fetch injecté."""
from app.ingestion.annuaires.cfai import (
    CfaiConnector, parse_fiche, parse_list_page, parse_total,
)

# Extrait RÉEL de cfai-annuaire-p1.html (table-list) — 2 lignes + 1 honoraire.
LIST_HTML = """
<table class="table table-striped table-hover table-list"><thead><tr>
<th>CP</th><th>Ville</th><th>Nom</th><th>Société</th><th></th></tr></thead><tbody>
<tr><td>75015</td><td>PARIS</td><td><b>ALEZRA Franck</b></td>
<td>SARL METROPOLE CONCEPT</td>
<td class="table-list-actions"><a href="/annuaire-professionnel/adherent/12"
class="btn btn-xs" title="Afficher"><i class="fa fa-eye"></i></a></td></tr>
<tr><td>33460</td><td>MACAU MEDOC</td><td><b>ARNAUDEAU François</b></td><td></td>
<td class="table-list-actions"><a href="/annuaire-professionnel/adherent/17"
class="btn btn-xs" title="Afficher"><i class="fa fa-eye"></i></a></td></tr>
</tbody></table>
<span class="badge bg-secondary">738 résultats</span>
"""

# Extrait RÉEL de cfai-adherent-12.html (fiche complète, cible).
FICHE_OK = """
<header><h1>Franck ALEZRA</h1>
<p class="member-company">SARL METROPOLE CONCEPT</p>
<p class="member-activity">Architecte d'Intérieur</p></header>
<h2>Contact</h2><h3>Adresse</h3>
<div class="details-group">13 rue Mademoiselle<br/>75015 PARIS</div>
<h3>Téléphones/fax</h3><div class="details-group">01 53 68 91 80</div>
<h3>Email</h3><div class="details-group">
<a href="mailto:alezra&#x40;metropole-concept.com">alezra@metropole-concept.com</a></div>
<h3>Site</h3><div class="details-group">
<a target="_blank" href="http://www.metropole-concept.com">www.metropole-concept.com</a></div>
"""

# Extrait RÉEL de cfai-adherent-17.html (honoraire → écarté).
FICHE_HONORAIRE = """
<header><h1>François ARNAUDEAU</h1>
<p class="member-activity">architecte d'intérieur DESLT</p>
<p class="member-activity-summary">Membre Honoraire du CFAI</p></header>
"""


def test_parse_list_page_extracts_rows():
    rows = parse_list_page(LIST_HTML)
    assert len(rows) == 2
    r = rows[0]
    assert r["fiche_id"] == "12"
    assert r["fiche_url"] == "https://www.cfai.fr/annuaire-professionnel/adherent/12"
    assert r["nom"] == "ALEZRA Franck"
    assert r["societe"] == "SARL METROPOLE CONCEPT"
    assert r["cp"] == "75015" and r["ville"] == "PARIS"


def test_parse_total():
    assert parse_total(LIST_HTML) == 738
    assert parse_total("<div>pas de badge</div>") is None


def test_parse_fiche_complete_target():
    f = parse_fiche(FICHE_OK, "12")
    assert f is not None
    assert f["name"] == "Franck ALEZRA"
    assert f["company"] == "SARL METROPOLE CONCEPT"
    assert f["phone"] == "01 53 68 91 80"
    assert f["email"] == "alezra@metropole-concept.com"
    assert f["website"] == "http://www.metropole-concept.com"
    assert "75015" in f["address"] and f["is_honoraire"] is False


def test_parse_fiche_honoraire_is_dropped():
    # Garde #2 (sonde) : Membre Honoraire = retraité → parse_fiche renvoie None.
    assert parse_fiche(FICHE_HONORAIRE, "17") is None


def test_connector_fetch_paginates_and_drops_honoraire():
    # http_fetch injecté : liste page 1 (2 lignes dont 1 honoraire), fiches par id.
    pages = {
        "https://www.cfai.fr/fr/recherche/annuaire-professionnel?page=1": LIST_HTML,
        "https://www.cfai.fr/annuaire-professionnel/adherent/12": FICHE_OK,
        "https://www.cfai.fr/annuaire-professionnel/adherent/17": FICHE_HONORAIRE,
    }
    calls = []

    def fake(url):
        calls.append(url)
        return pages.get(url)

    conn = CfaiConnector(http_fetch=fake)
    records = conn.fetch(since_days=0, limit=100, max_pages=1)
    # 1 seule fiche cible (l'honoraire est écarté).
    assert len(records) == 1 and records[0]["name"] == "Franck ALEZRA"
    assert conn.last_total_count == 738
    # Throttle : on n'a pas re-fetché deux fois la même URL.
    assert len(calls) == len(set(calls))


def test_to_candidates_maps_architecte_annuaire():
    conn = CfaiConnector(http_fetch=lambda u: None)
    cand = conn.to_candidates([{
        "name": "Franck ALEZRA", "company": "SARL METROPOLE CONCEPT",
        "activity": "Architecte d'Intérieur", "address": "13 rue Mademoiselle, 75015 PARIS",
        "city": "PARIS", "phone": "01 53 68 91 80",
        "email": "alezra@metropole-concept.com", "website": "http://www.metropole-concept.com",
        "fiche_id": "12", "fiche_url": "https://www.cfai.fr/annuaire-professionnel/adherent/12",
        "is_honoraire": False,
    }])[0]
    assert cand.source == "annuaire" and cand.source_ref == "cfai:12"
    assert cand.population == "architecte"
    assert cand.lifecycle_label == "studio_actif"
    assert cand.main_signal == "prescripteur actif"
    assert cand.establishment_name == "SARL METROPOLE CONCEPT"
    assert cand.decision_maker == "Franck ALEZRA"
    assert "annuaire cfai" in cand.secondary_signals
    assert cand.email == "alezra@metropole-concept.com"
    assert cand.establishment_type == "architecte d'intérieur"


def test_to_candidates_falls_back_to_person_name_without_company():
    conn = CfaiConnector(http_fetch=lambda u: None)
    cand = conn.to_candidates([{
        "name": "Alain AURIERES", "company": "", "activity": "", "address": "",
        "city": "", "phone": "", "email": None, "website": None,
        "fiche_id": "21", "fiche_url": "x", "is_honoraire": False,
    }])[0]
    assert cand.establishment_name == "Alain AURIERES"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_cfai_connector.py -q`
Expected: FAIL — `ModuleNotFoundError: app.ingestion.annuaires.cfai`.

- [ ] **Step 3: Write the implementation**

**a)** `beautifulsoup4` est **ABSENT** (confirmé : `requirements.txt` = `requests==2.32.3` seul, et `python -c "import bs4"` échoue dans `.venv`). L'ajouter à `backend/requirements.txt` (`beautifulsoup4>=4.12`) ET l'installer **maintenant** (`.venv\Scripts\python.exe -m pip install "beautifulsoup4>=4.12"`) — impératif AVANT que T4 ne fasse importer les connecteurs par `pipeline.py`, faute de quoi toute la suite de tests casse à la collecte (`ModuleNotFoundError: bs4`).

**b)** `backend/app/ingestion/annuaires/__init__.py` : fichier vide (package).

**c)** `backend/app/ingestion/annuaires/http.py` :

```python
"""HTTP POLI et injectable, partagé par les connecteurs d'annuaire (A2).

Throttle 2,5 s, User-Agent honnête, fail-soft. `polite_get` est le défaut réseau ;
les tests injectent un `HtmlFetch` factice alimenté par les HTML sondés."""
from __future__ import annotations

import time
from typing import Callable, Optional

import requests

# URL -> HTML texte, ou None (page illisible / interdite / erreur réseau).
HtmlFetch = Callable[[str], Optional[str]]

USER_AGENT = "Ambient Home lead research (contact: alexis.fauxbaton@gmail.com)"
_MIN_INTERVAL = 2.5  # scraping poli : >= 2,5 s entre deux requêtes
_last_call = [0.0]


def polite_get(url: str) -> Optional[str]:
    """GET throttlé (2,5 s), User-Agent honnête, fail-soft None."""
    wait = _MIN_INTERVAL - (time.monotonic() - _last_call[0])
    if wait > 0:
        time.sleep(wait)
    _last_call[0] = time.monotonic()
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
        resp.raise_for_status()
        return resp.text
    except Exception:
        return None
```

**d)** `backend/app/ingestion/annuaires/cfai.py` :

```python
"""Connecteur CFAI — annuaire des architectes d'intérieur (A2, brique annuaires).

Source de STOCK qualifiée : membres du Conseil Français des Architectes d'Intérieur
= ~100 % de la cible par construction (sonde-a2.json). HTML statique pur (aucun JS,
confirmé par la sonde), pagination GET `?page=N` (15 lignes/page, 738 total).
Robots CFAI permissif. Seul bruit : les « Membres Honoraires » (retraités) —
écartés déterministement (parse_fiche -> None). Fail-soft partout."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup

from ..base import Connector, LeadCandidate
from .http import HtmlFetch, polite_get

BASE = "https://www.cfai.fr"
LIST_URL = f"{BASE}/fr/recherche/annuaire-professionnel"


def parse_list_page(html: str) -> List[Dict[str, str]]:
    """Lignes de la table annuaire -> [{fiche_id, cp, ville, nom, societe, fiche_url}].
    Ignore toute ligne sans lien fiche. PURE."""
    soup = BeautifulSoup(html or "", "html.parser")
    out: List[Dict[str, str]] = []
    table = soup.select_one("table.table-list")
    if table is None:
        return out
    for tr in table.select("tbody tr"):
        link = tr.select_one("a[href*='/adherent/']")
        if link is None:
            continue
        href = link.get("href", "")
        m = re.search(r"/adherent/(\d+)", href)
        if not m:
            continue
        tds = tr.find_all("td")

        def _txt(i: int) -> str:
            return tds[i].get_text(" ", strip=True) if i < len(tds) else ""

        out.append({
            "fiche_id": m.group(1),
            "cp": _txt(0),
            "ville": _txt(1),
            "nom": _txt(2),
            "societe": _txt(3),
            "fiche_url": href if href.startswith("http") else f"{BASE}{href}",
        })
    return out


def parse_total(html: str) -> Optional[int]:
    """Entier du badge « N résultats », ou None. PURE."""
    soup = BeautifulSoup(html or "", "html.parser")
    badge = soup.select_one("span.badge")
    if badge is None:
        return None
    m = re.search(r"(\d[\d\s]*)\s*résultats", badge.get_text(" ", strip=True))
    return int(m.group(1).replace(" ", "")) if m else None


def parse_fiche(html: str, fiche_id: str) -> Optional[Dict[str, Any]]:
    """Fiche adhérent -> dict, ou None si Membre Honoraire (garde sonde #2) ou
    <h1> absent. PURE. Extrait nom/société/activité/adresse/tél/email/site."""
    soup = BeautifulSoup(html or "", "html.parser")
    h1 = soup.select_one("h1")
    if h1 is None:
        return None
    name = h1.get_text(" ", strip=True)
    summary = soup.select_one(".member-activity-summary")
    is_honoraire = bool(summary and "honoraire" in summary.get_text(" ", strip=True).lower())
    if is_honoraire:
        return None  # retraité : pas de valeur commerciale
    company_el = soup.select_one(".member-company")
    activity_el = soup.select_one(".member-activity")

    # Sections Contact : chaque <h3> titre -> le .details-group qui suit.
    def _group_after(title_kw: str) -> str:
        for h3 in soup.select("h3"):
            if title_kw in h3.get_text(" ", strip=True).lower():
                grp = h3.find_next_sibling(class_="details-group")
                if grp:
                    return grp.get_text(" ", strip=True)
        return ""

    address = _group_after("adresse")
    phone = _group_after("téléphone") or _group_after("telephone")
    mail = soup.select_one("a[href^='mailto:']")
    email = mail.get("href", "")[len("mailto:"):].strip() if mail else None
    # Site : lien externe dans la section Site (pas les réseaux du footer).
    website = None
    for h3 in soup.select("h3"):
        if h3.get_text(" ", strip=True).lower().startswith("site"):
            grp = h3.find_next_sibling(class_="details-group")
            a = grp.select_one("a[href]") if grp else None
            if a:
                website = a.get("href", "").strip()
            break

    m = re.search(r"\b(\d{5})\b", address)
    city = ""
    if m:
        city = address[m.end():].strip(" ,")

    return {
        "fiche_id": fiche_id,
        "name": name,
        "company": company_el.get_text(" ", strip=True) if company_el else "",
        "activity": activity_el.get_text(" ", strip=True) if activity_el else "",
        "address": address,
        "city": city,
        "phone": phone,
        "email": email or None,
        "website": website,
        "is_honoraire": False,
    }


class CfaiConnector(Connector):
    """Crawler CFAI : pagine la liste (GET), puis fetch chaque fiche (bornée par
    `limit`). Honoraires écartés. HTTP injectable (tests sans réseau)."""
    name = "cfai"

    def __init__(self, http_fetch: HtmlFetch = polite_get) -> None:
        self.http_fetch = http_fetch
        self.last_total_count = 0

    def fetch(self, since_days: int = 0, limit: int = 800,
              max_pages: int = 60, **_: Any) -> List[Dict[str, Any]]:
        rows: List[Dict[str, str]] = []
        for page in range(1, (max_pages or 1) + 1):
            html = self.http_fetch(f"{LIST_URL}?page={page}")
            if not html:
                break
            if page == 1:
                total = parse_total(html)
                if total is not None:
                    self.last_total_count = total
            page_rows = parse_list_page(html)
            if not page_rows:
                break  # plus de lignes : fin de pagination
            rows.extend(page_rows)
            if len(rows) >= limit:
                break
        if not self.last_total_count:
            self.last_total_count = len(rows)

        out: List[Dict[str, Any]] = []
        for row in rows[:limit]:
            html = self.http_fetch(row["fiche_url"])
            if not html:
                continue
            fiche = parse_fiche(html, row["fiche_id"])
            if fiche is None:
                continue  # honoraire ou fiche illisible
            fiche["fiche_url"] = row["fiche_url"]
            fiche.setdefault("city", row.get("ville", ""))
            out.append(fiche)
        return out

    def to_candidates(self, records: List[Dict[str, Any]]) -> List[LeadCandidate]:
        from datetime import date
        today = date.today()
        out: List[LeadCandidate] = []
        for f in records:
            company = (f.get("company") or "").strip()
            name = (f.get("name") or "").strip()
            proof = "Architecte d'intérieur membre du CFAI (annuaire professionnel)."
            out.append(LeadCandidate(
                source="annuaire",
                source_ref=f"cfai:{f['fiche_id']}",
                establishment_name=company or name,
                city=f.get("city") or "",
                address=f.get("address") or "",
                main_signal="prescripteur actif",
                secondary_signals=["annuaire cfai"],
                lifecycle_label="studio_actif",
                population="architecte",
                establishment_type="architecte d'intérieur",
                decision_maker=name or None,
                detection_date=today,
                classification_text=" ".join(filter(None, [company, name, f.get("activity")])),
                email=f.get("email"),
                website=f.get("website"),
                proof_text=proof,
                proof_url=f.get("fiche_url") or "",
            ))
        return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_cfai_connector.py -q` → PASS (7 tests).
Run: `python -m pytest tests/ -q` → tout vert (package neuf, aucun import existant modifié).

- [ ] **Step 5: Commit**

```bash
git add backend/app/ingestion/annuaires backend/tests/test_cfai_connector.py backend/requirements.txt
git commit -m "feat(annuaires): connecteur CFAI (HTML statique, pagination GET, filtre honoraire)"
```

---

### Task 2: Connecteur UFDI (annuaire, signal hospitality natif → tier T2)

**Modèle d'exécution recommandé : sonnet**

**Files:**
- Create: `backend/app/ingestion/annuaires/ufdi.py`
- Create: `backend/tests/test_ufdi_connector.py`

**Interfaces:**
- `ufdi.py` — parsing **PUR** + connecteur :
  - `BASE = "https://www.ufdi.fr"`, `FRANCE_URL = f"{BASE}/decorateur/decorateurs-france-fr.html"` (recensement national, **~157 profils réels** en un fetch — PAS 255 : les ~98 liens dept-nav ne sont pas des cartes team_member, décision sonde #4), `REGION_URLS` (15 pages régionales `/decorateur/decorateurs-region-*.html`, repli si la page France échoue).
  - `parse_list_page(html) -> List[Dict[str, str]]` : cartes `div.et_pb_team_member` → `[{name, societe, ville, profile_url, slug}]` (`h4>a[href]`, `h5`, `h6`). Ignore les cartes sans lien `/decorateur/*-<id>.html`. PURE.
  - `parse_profile(html) -> Dict[str, Any]` : `{name, city, phone, website, instagram, activities, hospitality}` — `name`/`city` du `<title>` (`Nom • … à Ville CP • UFDI`) et du `.et_pb_fullwidth_header_subhead` ; `phone` via `data-numero` ; `website` via `a.site[href]` ; `instagram` via `a[href*='instagram.com']` (hors comptes UFDI officiels `ufdideco`/`ufdidecoarchi`) ; `activities` = liste des `<li>` « Décoration … » ; `hospitality = True` si « Décoration Hôtels » ou « Décoration Restaurants » présent (**décision sonde #5**). PURE.
  - `UfdiConnector(Connector)` : `name = "ufdi"`. `fetch(limit, **_)` : fetch `FRANCE_URL` (repli régions si vide), parse la liste, puis fetch chaque profil (borné `limit`), fusionne carte (société) + profil (contact/hospitality). Pose `self.last_total_count`. HTTP injectable. `to_candidates` : `LeadCandidate(source="annuaire", source_ref=f"ufdi:{slug}", population="architecte", lifecycle_label="studio_actif", main_signal="prescripteur actif", establishment_name=societe or name, decision_maker=name, city, phone (→ `raw['phone']` puis contact en T4/enrichissement ; on stocke le tél dans `proof`/`raw`), website, instagram, secondary_signals=["annuaire ufdi"] + (["portfolio hospitality/CHR"] si hospitality))`.
- **UFDI n'expose PAS d'email en clair** (sonde #4) → `email=None` (l'enrichissement contact T4/Places pourra le combler). Le **téléphone** est en clair (`data-numero`) → posé sur `LeadCandidate` via un nouveau champ ? Non : `LeadCandidate` n'a pas de `phone` (le tél vit sur `Opportunity.phone`, rempli par la passe contact). On transporte le tél via `raw={"phone": ...}` et `proof_text`, et T4 le recopiera sur `Opportunity.phone` à la création (petit ajout ciblé). *(Décision T4 : `_process_candidate` lit `cand.raw.get("phone")` pour les leads annuaire.)*

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_ufdi_connector.py
"""Connecteur UFDI (A2, T2) — parsing PUR sur extraits des HTML sondés
(.superpowers/sdd/sonde-a2/ufdi-*.html). Aucun réseau."""
from app.ingestion.annuaires.ufdi import (
    UfdiConnector, parse_list_page, parse_profile,
)

# Extrait RÉEL d'une carte de ufdi-france.html (et_pb_team_member).
LIST_HTML = """
<div class="et_pb_team_member b3_team clearfix">
<div class="et_pb_team_member_image"><a
href="https://www.ufdi.fr/decorateur/berenice-alandi-agence-berenice-alandi-29000-quimper-169.html">
<img title="Bérénice ALANDI" alt="Bérénice ALANDI"/></a></div>
<div class="et_pb_team_member_description">
<h4 class="et_pb_module_header"><a
href="https://www.ufdi.fr/decorateur/berenice-alandi-agence-berenice-alandi-29000-quimper-169.html">Bérénice ALANDI</a></h4>
<h5>Agence Bérénice Alandi</h5><h6>Quimper</h6></div></div>
"""

# Extrait RÉEL de ufdi-profile-kokocinski.html (hospitality: Hôtels + Restaurants).
PROFILE_HOSPITALITY = """
<title>Cécile KOKOCINSKI &#8226; Décorateur et Architecte d'intérieur à Paris 75007 &#8226; UFDI</title>
<span class="et_pb_fullwidth_header_subhead">Paris</span>
<a class="numero" data-numero="0756865040">Téléphone</a>
<a href="https://www.cecilekokocinski.fr" class="site" title="Site Internet">Site Internet</a>
<a href="https://www.instagram.com/cecile_kokocinski/?hl=fr">Instagram</a>
<a href="https://www.instagram.com/ufdidecoarchi/">UFDI</a>
<ul><li>Décoration Bureaux</li><li>Décoration Commerces</li>
<li>Décoration Hôtels</li><li>Décoration Restaurants</li></ul>
"""

# Extrait RÉEL de ufdi-profile-benedetti.html (SANS hospitality).
PROFILE_NO_HOSPITALITY = """
<title>Delphine BENEDETTI &#8226; Décorateur d'intérieur à Paris 75015 &#8226; UFDI</title>
<span class="et_pb_fullwidth_header_subhead">Paris</span>
<a class="numero" data-numero="0660439112">Téléphone</a>
<ul><li>Décoration Commerces</li><li>Home staging</li><li>Home organising</li></ul>
"""


def test_parse_list_page():
    rows = parse_list_page(LIST_HTML)
    assert len(rows) == 1
    r = rows[0]
    assert r["name"] == "Bérénice ALANDI"
    assert r["societe"] == "Agence Bérénice Alandi"
    assert r["ville"] == "Quimper"
    assert r["slug"] == "berenice-alandi-agence-berenice-alandi-29000-quimper-169"
    assert r["profile_url"].endswith(".html")


def test_parse_profile_hospitality_native_tag():
    # Décision sonde #5 : Hôtels/Restaurants -> hospitality True (tier T2).
    p = parse_profile(PROFILE_HOSPITALITY)
    assert p["name"].startswith("Cécile KOKOCINSKI")
    assert p["city"] == "Paris"
    assert p["phone"] == "0756865040"
    assert p["website"] == "https://www.cecilekokocinski.fr"
    assert p["instagram"] == "cecile_kokocinski"  # compte UFDI officiel exclu
    assert p["hospitality"] is True
    assert "Décoration Hôtels" in p["activities"]


def test_parse_profile_without_hospitality():
    p = parse_profile(PROFILE_NO_HOSPITALITY)
    assert p["phone"] == "0660439112"
    assert p["hospitality"] is False
    assert p["website"] is None and p["instagram"] is None


def test_connector_fetch_merges_card_and_profile():
    pages = {
        "https://www.ufdi.fr/decorateur/decorateurs-france-fr.html": LIST_HTML,
        "https://www.ufdi.fr/decorateur/berenice-alandi-agence-berenice-alandi-29000-quimper-169.html":
            PROFILE_HOSPITALITY,
    }
    conn = UfdiConnector(http_fetch=lambda u: pages.get(u))
    records = conn.fetch(limit=50)
    assert len(records) == 1
    r = records[0]
    assert r["societe"] == "Agence Bérénice Alandi"  # de la carte
    assert r["hospitality"] is True                   # du profil
    assert r["phone"] == "0756865040"
    assert conn.last_total_count == 1


def test_to_candidates_hospitality_gets_t2_secondary():
    conn = UfdiConnector(http_fetch=lambda u: None)
    cand = conn.to_candidates([{
        "name": "Cécile KOKOCINSKI", "societe": "Cecile Kokocinski Studio",
        "city": "Paris", "slug": "cecile-kokocinski-75007-paris-1",
        "profile_url": "https://www.ufdi.fr/decorateur/cecile-kokocinski-75007-paris-1.html",
        "phone": "0756865040", "website": "https://www.cecilekokocinski.fr",
        "instagram": "cecile_kokocinski", "hospitality": True,
        "activities": ["Décoration Hôtels", "Décoration Restaurants"],
    }])[0]
    assert cand.source == "annuaire" and cand.source_ref == "ufdi:cecile-kokocinski-75007-paris-1"
    assert cand.population == "architecte"
    assert cand.establishment_name == "Cecile Kokocinski Studio"
    assert cand.decision_maker == "Cécile KOKOCINSKI"
    assert "annuaire ufdi" in cand.secondary_signals
    assert "portfolio hospitality/CHR" in cand.secondary_signals  # tier T2
    assert cand.instagram == "cecile_kokocinski"
    assert cand.email is None                       # UFDI : pas d'email en clair
    assert cand.raw.get("phone") == "0756865040"   # tél transporté via raw (T4)


def test_to_candidates_no_hospitality_stays_t3():
    conn = UfdiConnector(http_fetch=lambda u: None)
    cand = conn.to_candidates([{
        "name": "Delphine BENEDETTI", "societe": "DBinteriors", "city": "Paris",
        "slug": "delphine-benedetti-75015-paris-2", "profile_url": "x",
        "phone": "0660439112", "website": None, "instagram": None,
        "hospitality": False, "activities": ["Décoration Commerces"],
    }])[0]
    assert "portfolio hospitality/CHR" not in cand.secondary_signals
    assert cand.secondary_signals == ["annuaire ufdi"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_ufdi_connector.py -q`
Expected: FAIL — `ModuleNotFoundError: app.ingestion.annuaires.ufdi`.

- [ ] **Step 3: Write the implementation**

`backend/app/ingestion/annuaires/ufdi.py` :

```python
"""Connecteur UFDI — annuaire des décorateurs/architectes d'intérieur (A2).

Source de STOCK : membres de l'Union Francophone des Décorateurs d'Intérieur.
WordPress/Divi statique (aucun JS, sonde). Découverte via la page nationale
`/decorateur/decorateurs-france-fr.html` (~157 profils réels en un fetch ; les
~98 liens de navigation départementale de la page NE SONT PAS des cartes
team_member et sont exclus par le scope `div.et_pb_team_member`) ou les 15
pages régionales (repli). Robots UFDI : `/decorateur/*.html` ALLOW (on n'utilise
QUE ce chemin ; `/membres.php` DISALLOW n'est jamais touché). Signal hospitality
NATIF (`<li>Décoration Hôtels/Restaurants</li>`) -> tier T2. Pas d'email en clair
(-> enrichissement contact aval). Téléphone en clair via `data-numero`."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup

from ..base import Connector, LeadCandidate
from .http import HtmlFetch, polite_get

BASE = "https://www.ufdi.fr"
FRANCE_URL = f"{BASE}/decorateur/decorateurs-france-fr.html"
REGION_SLUGS = [
    "auvergne-rhone-alpes", "bourgogne-franche-comte", "bretagne",
    "centre-val-de-loire", "champagne-ardennes", "corse", "cote-d-azur",
    "grand-est", "hauts-de-france", "ile-de-france", "normandie",
    "nouvelle-aquitaine", "occitanie-est", "occitanie-ouest",
    "pays-de-la-loire", "provence",
]
REGION_URLS = [f"{BASE}/decorateur/decorateurs-region-{s}.html" for s in REGION_SLUGS]

# Comptes Instagram officiels UFDI (jamais le studio lui-même).
_UFDI_IG = {"ufdideco", "ufdidecoarchi"}
_PROFILE_RE = re.compile(r"/decorateur/([a-z0-9-]+-\d+)\.html")
_HOSPITALITY = ("Décoration Hôtels", "Décoration Restaurants")


def parse_list_page(html: str) -> List[Dict[str, str]]:
    """Cartes team_member -> [{name, societe, ville, profile_url, slug}]. PURE."""
    soup = BeautifulSoup(html or "", "html.parser")
    out: List[Dict[str, str]] = []
    seen = set()
    for card in soup.select("div.et_pb_team_member"):
        a = card.select_one("h4 a[href*='/decorateur/']") or card.select_one(
            "a[href*='/decorateur/']")
        if a is None:
            continue
        href = a.get("href", "")
        m = _PROFILE_RE.search(href)
        if not m or m.group(1) in seen:
            continue
        seen.add(m.group(1))
        h5 = card.select_one("h5")
        h6 = card.select_one("h6")
        out.append({
            "name": a.get_text(" ", strip=True),
            "societe": h5.get_text(" ", strip=True) if h5 else "",
            "ville": h6.get_text(" ", strip=True) if h6 else "",
            "slug": m.group(1),
            "profile_url": href if href.startswith("http") else f"{BASE}{href}",
        })
    return out


def parse_profile(html: str) -> Dict[str, Any]:
    """Fiche décorateur -> {name, city, phone, website, instagram, activities,
    hospitality}. PURE. Téléphone via data-numero (aucun JS, sonde)."""
    soup = BeautifulSoup(html or "", "html.parser")
    title = soup.select_one("title")
    title_txt = title.get_text(" ", strip=True) if title else ""
    name = title_txt.split("•")[0].split("•")[0].strip()
    subhead = soup.select_one(".et_pb_fullwidth_header_subhead")
    city = subhead.get_text(" ", strip=True) if subhead else ""

    numero = soup.select_one("[data-numero]")
    phone = numero.get("data-numero", "").strip() if numero else None

    site = soup.select_one("a.site[href]")
    website = site.get("href", "").strip() if site else None

    instagram = None
    for a in soup.select("a[href*='instagram.com']"):
        m = re.search(r"instagram\.com/([A-Za-z0-9_.]+)", a.get("href", ""))
        if m and m.group(1).lower() not in _UFDI_IG:
            instagram = m.group(1)
            break

    activities = [li.get_text(" ", strip=True) for li in soup.select("li")
                  if li.get_text(" ", strip=True).startswith("Décoration")]
    hospitality = any(h in activities for h in _HOSPITALITY)

    return {"name": name, "city": city, "phone": phone or None, "website": website,
            "instagram": instagram, "activities": activities, "hospitality": hospitality}


class UfdiConnector(Connector):
    """Crawler UFDI : page nationale (repli régions), puis fetch chaque profil."""
    name = "ufdi"

    def __init__(self, http_fetch: HtmlFetch = polite_get) -> None:
        self.http_fetch = http_fetch
        self.last_total_count = 0

    def _discover(self) -> List[Dict[str, str]]:
        html = self.http_fetch(FRANCE_URL)
        rows = parse_list_page(html) if html else []
        if rows:
            return rows
        # Repli : agréger les pages régionales (dédup par slug via parse_list_page).
        seen: Dict[str, Dict[str, str]] = {}
        for url in REGION_URLS:
            h = self.http_fetch(url)
            for r in (parse_list_page(h) if h else []):
                seen.setdefault(r["slug"], r)
        return list(seen.values())

    def fetch(self, limit: int = 300, **_: Any) -> List[Dict[str, Any]]:
        rows = self._discover()
        self.last_total_count = len(rows)
        out: List[Dict[str, Any]] = []
        for row in rows[:limit]:
            html = self.http_fetch(row["profile_url"])
            prof = parse_profile(html) if html else {}
            merged = dict(row)
            merged.update({
                "name": prof.get("name") or row.get("name") or "",
                # La carte (h6) porte la COMMUNE précise ; le sous-titre du profil
                # (.et_pb_fullwidth_header_subhead) est parfois un DÉPARTEMENT
                # (ex. « Hauts-de-Seine », sonde) -> on préfère la ville de la carte
                # pour ne pas dégrader la cohérence géo du matcher/dédup.
                "city": row.get("ville") or prof.get("city") or "",
                "department": prof.get("city") or "",
                "phone": prof.get("phone"),
                "website": prof.get("website"),
                "instagram": prof.get("instagram"),
                "hospitality": bool(prof.get("hospitality")),
                "activities": prof.get("activities") or [],
            })
            out.append(merged)
        return out

    def to_candidates(self, records: List[Dict[str, Any]]) -> List[LeadCandidate]:
        from datetime import date
        today = date.today()
        out: List[LeadCandidate] = []
        for r in records:
            societe = (r.get("societe") or "").strip()
            name = (r.get("name") or "").strip()
            secondary = ["annuaire ufdi"]
            if r.get("hospitality"):
                secondary.append("portfolio hospitality/CHR")  # tier T2 (sonde #5)
            proof = "Décorateur/architecte d'intérieur membre de l'UFDI (annuaire)."
            if r.get("hospitality"):
                proof += " Fiche UFDI : Décoration Hôtels/Restaurants (signal CHR)."
            out.append(LeadCandidate(
                source="annuaire",
                source_ref=f"ufdi:{r['slug']}",
                establishment_name=societe or name,
                city=r.get("city") or "",
                main_signal="prescripteur actif",
                secondary_signals=secondary,
                lifecycle_label="studio_actif",
                population="architecte",
                establishment_type="architecte d'intérieur",
                decision_maker=name or None,
                instagram=r.get("instagram"),
                website=r.get("website"),
                email=None,  # UFDI : pas d'email en clair (sonde)
                detection_date=today,
                classification_text=" ".join(filter(None, [societe, name])),
                proof_text=proof,
                proof_url=r.get("profile_url") or "",
                raw={"phone": r.get("phone"), "activities": r.get("activities") or []},
            ))
        return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_ufdi_connector.py -q` → PASS (6 tests).
Run: `python -m pytest tests/ -q` → tout vert.

- [ ] **Step 5: Commit**

```bash
git add backend/app/ingestion/annuaires/ufdi.py backend/tests/test_ufdi_connector.py
git commit -m "feat(annuaires): connecteur UFDI (statique, telephone data-numero, signal hospitality natif -> tier T2)"
```

---

### Task 3: Connecteur délta jeunes studios (Sirene archi, filtre de qualification mesuré)

**Modèle d'exécution recommandé : sonnet**

**Files:**
- Create: `backend/app/ingestion/jeunes_studios.py`
- Create: `backend/tests/test_jeunes_studios.py`

**Interfaces:**
- `ARCHI_NAF_CODES = ["71.11Z", "74.10Z"]` (sonde volet 2).
- `QUALIF_KEYWORDS` (normalisés sans accent, sonde #9) : `interieur, design, studio, agencement, deco, archi, atelier, concept, home, espace`.
- `NEG_KEYWORDS` (garde négatif anti-bruit 74.10Z, sonde #9) : `graphique, graphic, graphisme, web, ux, ui, packaging, motion`.
- `qualifies(name) -> bool` (PURE) : `True` si un `QUALIF_KEYWORD` est présent ET aucun `NEG_KEYWORD`. Une dénomination vide/`[ND]` → `False`.
- `map_jeune_studio(etab, today) -> Optional[LeadCandidate]` (PURE, calquée sur `sirene_delta.map_etablissement` mais population archi) : `None` si fermé, hors NAF archi, dénomination masquée/absente, ou `not qualifies(name)`. Sinon `LeadCandidate(source="jeunes_studios", source_ref=siret, population="architecte", establishment_type="architecte d'intérieur", main_signal="prescripteur actif", secondary_signals=["jeune studio (création récente)"], lifecycle_label="unknown", siren/siret/naf natifs, siren_match_method="source", activity_start_date=dateCreation, decision_maker=prénom+nom si personne physique, proof_text)`.
- `JeunesStudiosConnector(Connector)` : `name = "jeunes_studios"`. `fetch(since_days=30, limit=1000, departments, since_date, max_pages, **_)` : réutilise `insee.fetch_new_etablissements` avec `ARCHI_NAF_CODES`, fenêtre `[date_from, today]` (**PAS d'horizon futur** — jeunes studios déjà créés), `cp_prefixes` selon `departments` (défaut France entière `None`, la valeur `["france"]` ou `None` → France ; une liste → préfixes). Pose `self.last_total_count = meta['total']`. `to_candidates` → `map_jeune_studio` filtré.
- Réutilise `sirene_delta._nd`, `_best_name`, `_address`, `_ymd` (import depuis `sirene_delta`).

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_jeunes_studios.py
"""Connecteur délta jeunes studios (A2, T3) — mapping PUR, aucun réseau.
Grounded sur le rendement mesuré par la sonde (sonde-a2.json volet 2)."""
from datetime import date

from app.ingestion.jeunes_studios import (
    ARCHI_NAF_CODES, JeunesStudiosConnector, map_jeune_studio, qualifies,
)

TODAY = date(2026, 7, 11)


def _etab(naf="71.11Z", enseigne=None, denom=None, prenom=None, nom=None,
          siret="12345678900011", created="2026-06-20", etat="A", nd=False):
    ul = {}
    if denom:
        ul["denominationUniteLegale"] = denom
    if prenom:
        ul["prenom1UniteLegale"] = prenom
    if nom:
        ul["nomUniteLegale"] = nom
    per = {"etatAdministratifEtablissement": etat,
           "activitePrincipaleEtablissement": naf}
    if enseigne:
        per["enseigne1Etablissement"] = "[ND]" if nd else enseigne
    return {"siret": siret, "siren": siret[:9], "uniteLegale": ul,
            "periodesEtablissement": [per], "etablissementSiege": True,
            "dateCreationEtablissement": created,
            "adresseEtablissement": {"libelleCommuneEtablissement": "PARIS",
                                     "codePostalEtablissement": "75011"}}


def test_qualifies_keyword_hit():
    assert qualifies("STUDIO GHIRIBELLI")
    assert qualifies("Le Gambit Architecture d'Interieur")
    assert qualifies("ATELIER EL MANSOURY")


def test_qualifies_rejects_empty_and_neg_keyword():
    assert not qualifies("")
    assert not qualifies("[ND]")
    assert not qualifies("SIXCOM")                       # pas de mot métier
    assert not qualifies("LEA LAXTON DESIGN GRAPHIQUE")  # 74.10Z graphisme (garde neg)


def test_map_qualified_studio():
    etab = _etab(denom="MANOA DESIGN", siret="99988877700022", created="2026-06-25")
    c = map_jeune_studio(etab, TODAY)
    assert c is not None
    assert c.source == "jeunes_studios" and c.source_ref == "99988877700022"
    assert c.population == "architecte"
    assert c.lifecycle_label == "unknown"
    assert c.main_signal == "prescripteur actif"
    assert "jeune studio (création récente)" in c.secondary_signals
    assert c.siren == "999888777" and c.naf == "71.11Z"
    assert c.siren_match_method == "source"
    assert c.activity_start_date == date(2026, 6, 25)


def test_map_personne_physique_sets_decision_maker():
    etab = _etab(denom=None, prenom="Camille", nom="Durand")
    # Personne physique nommée SANS mot-clé métier -> non qualifiée (sonde #9).
    assert map_jeune_studio(etab, TODAY) is None
    etab2 = _etab(denom=None, prenom="Camille", nom="Durand",
                  enseigne="STUDIO CAMILLE DESIGN")
    c = map_jeune_studio(etab2, TODAY)
    assert c is not None and c.decision_maker == "Camille Durand"


def test_map_drops_masked_closed_and_nonarchi():
    assert map_jeune_studio(_etab(denom="STUDIO X", etat="F"), TODAY) is None
    assert map_jeune_studio(_etab(denom="STUDIO X", naf="56.10A"), TODAY) is None
    # Dénomination masquée [ND] partout -> injoignable ET inqualifiable.
    masked = _etab(denom=None, enseigne="STUDIO Y", nd=True)
    assert map_jeune_studio(masked, TODAY) is None


def test_connector_fetch_uses_archi_naf_and_no_future(monkeypatch):
    monkeypatch.setenv("INSEE_API_KEY", "test-key")
    captured = {}

    def fake_fetch_new(date_from, date_to, naf_codes, cp_prefixes=None,
                       limit=3000, fetch=None, meta=None):
        captured["naf"] = list(naf_codes)
        captured["date_to"] = date_to
        captured["cp"] = cp_prefixes
        if meta is not None:
            meta["total"] = 1625
        return [_etab(denom="MANOA DESIGN")]

    import app.ingestion.jeunes_studios as js
    monkeypatch.setattr(js, "fetch_new_etablissements", fake_fetch_new)
    conn = JeunesStudiosConnector()
    records = conn.fetch(since_days=30, limit=1000)
    assert captured["naf"] == ARCHI_NAF_CODES
    assert captured["date_to"] == date.today()  # PAS d'horizon futur
    assert captured["cp"] is None               # France entière par défaut
    assert conn.last_total_count == 1625
    cands = conn.to_candidates(records)
    assert len(cands) == 1 and cands[0].source == "jeunes_studios"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_jeunes_studios.py -q`
Expected: FAIL — `ModuleNotFoundError: app.ingestion.jeunes_studios`.

- [ ] **Step 3: Write the implementation**

`backend/app/ingestion/jeunes_studios.py` :

```python
"""Connecteur délta JEUNES STUDIOS d'architecture d'intérieur (A2).

Réutilise la brique 2 (insee.fetch_new_etablissements, throttle 2,1 s intégré)
pointée sur NAF 71.11Z/74.10Z, fenêtre de création RÉCENTE (pas le stock).
Sonde-a2 volet 2 : flux RECALL-ORIENTÉ mais BRUYANT (91 % d'EI) et AVEUGLE
(65 % de dénominations masquées [ND]). Filtre de qualification mots-clés mesuré
(28 % des dénominations visibles ; ~5 studios qualifiables/jour) + garde négatif
anti-bruit 74.10Z (design graphique). Flux faible priorité -> lifecycle 'unknown',
PAS de tier. SIREN/dirigeant/ancienneté NATIFS (aucun matcher requis)."""
from __future__ import annotations

import unicodedata
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from .base import Connector, LeadCandidate
from .insee import fetch_new_etablissements
from .sirene_delta import IDF_CP_PREFIXES, _address, _best_name, _nd, _ymd

ARCHI_NAF_CODES = ["71.11Z", "74.10Z"]

# Mots-clés de qualification sur la dénomination (sonde #9, rendement 28 % visible).
QUALIF_KEYWORDS = ("interieur", "design", "studio", "agencement", "deco",
                   "archi", "atelier", "concept", "home", "espace")
# Garde négatif : le NAF 74.10Z couvre le design graphique/produit (bruit adjacent).
NEG_KEYWORDS = ("graphique", "graphic", "graphisme", "web", "ux", "ui",
                "packaging", "motion")


def _norm(text: Optional[str]) -> str:
    t = unicodedata.normalize("NFD", (text or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def qualifies(name: Optional[str]) -> bool:
    """True si la dénomination porte un mot-clé métier ET aucun mot-clé négatif.
    Vide / [ND] -> False (injoignable ET inqualifiable). PURE."""
    n = _norm(name)
    if not n or n == "[nd]":
        return False
    if any(neg in n for neg in NEG_KEYWORDS):
        return False
    return any(kw in n for kw in QUALIF_KEYWORDS)


def map_jeune_studio(etab: Dict[str, Any], today: date) -> Optional[LeadCandidate]:
    """Établissement INSEE archi -> LeadCandidate 'architecte', ou None (fermé,
    hors NAF archi, dénomination masquée/absente, ou non qualifiée). PURE."""
    per = (etab.get("periodesEtablissement") or [{}])[0]
    if (per.get("etatAdministratifEtablissement") or "A") != "A":
        return None
    naf = per.get("activitePrincipaleEtablissement")
    if naf not in ARCHI_NAF_CODES:
        return None
    name = _best_name(etab)  # enseigne > denom usuelle > denom UL > prénom+nom
    if not name or not qualifies(name):
        return None
    created = _ymd(etab.get("dateCreationEtablissement"))
    address, city = _address(etab)

    ul = etab.get("uniteLegale") or {}
    prenom, nom = _nd(ul.get("prenom1UniteLegale")), _nd(ul.get("nomUniteLegale"))
    decision_maker = f"{prenom.title()} {nom.title()}" if (prenom and nom) else None

    proof = (f"Studio récemment créé le {created.isoformat() if created else '?'} "
             f"au registre Sirene (NAF {naf}, activité de conception d'espaces).")

    return LeadCandidate(
        source="jeunes_studios",
        source_ref=etab.get("siret") or "",
        establishment_name=name,
        city=city or "",
        address=address,
        main_signal="prescripteur actif",
        secondary_signals=["jeune studio (création récente)"],
        lifecycle_label="unknown",
        population="architecte",
        establishment_type="architecte d'intérieur",
        decision_maker=decision_maker,
        detection_date=today,
        activity_start_date=created,
        classification_text=name,
        siren=etab.get("siren"),
        naf=naf,
        siret=etab.get("siret"),
        siren_match_method="source",
        proof_text=proof,
        raw=etab,
    )


class JeunesStudiosConnector(Connector):
    """Délta des nouveaux SIRET archi (INSEE). `departments` : None/['france'] ->
    France entière ; liste -> préfixes de CP. Fenêtre = `since_days` derniers jours
    jusqu'à aujourd'hui (PAS d'horizon futur : un jeune studio est déjà créé)."""
    name = "jeunes_studios"

    def __init__(self) -> None:
        self.last_total_count = 0

    def fetch(self, since_days: int = 30, limit: int = 1000,
              departments: Optional[List[str]] = None,
              since_date: Optional[date] = None, **_: Any) -> List[Dict[str, Any]]:
        today = date.today()
        date_from = since_date or (today - timedelta(days=since_days or 30))
        if departments is None or departments == ["france"]:
            cp_prefixes: Optional[List[str]] = None
        elif departments == ["idf"]:
            cp_prefixes = IDF_CP_PREFIXES
        else:
            cp_prefixes = departments
        meta: Dict[str, Any] = {}
        records = fetch_new_etablissements(
            date_from, today, ARCHI_NAF_CODES,
            cp_prefixes=cp_prefixes, limit=limit, meta=meta,
        )
        self.last_total_count = meta.get("total") or len(records)
        return records

    def to_candidates(self, records: List[Dict[str, Any]]) -> List[LeadCandidate]:
        today = date.today()
        out: List[LeadCandidate] = []
        for etab in records:
            cand = map_jeune_studio(etab, today)
            if cand:
                out.append(cand)
        return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_jeunes_studios.py -q` → PASS (6 tests).
Run: `python -m pytest tests/ -q` → tout vert (`sirene_delta` inchangé, imports réutilisés).

- [ ] **Step 5: Commit**

```bash
git add backend/app/ingestion/jeunes_studios.py backend/tests/test_jeunes_studios.py
git commit -m "feat(annuaires): connecteur delta jeunes studios (NAF archi, filtre qualification mesure, SIREN natif)"
```

---

### Task 4: Câblage — matcher archi + enrichissement SIREN + `run_annuaires` + dédup nom+ville + CONNECTORS/CLI/UI

**Modèle d'exécution recommandé : opus**

**Files:**
- Modify: `backend/app/ingestion/enrichment/naf_classifier.py` (`classify_naf_prescripteur`)
- Modify: `backend/app/ingestion/enrichment/siret_matcher.py` (`match_architecte`, `dirigeant_from_result` — chemin PARALLÈLE, `match()` CHR intact)
- Modify: `backend/app/ingestion/pipeline.py` (`CONNECTORS` += cfai/ufdi/jeunes_studios ; `SOURCE_LABELS` ; `IngestStats.soft_merges` (nouveau champ, paires fusionnées) ; `run_annuaires` ; `_soft_dedup_architecte` + `_corroborates` ; garde annuaire du tag de corroboration dans `_merge_corroboration` ; `_process_candidate` : phone depuis `raw` pour l'annuaire (création, upsert ET soft-merge) + branche fusion nom+ville avec enregistrement de la paire)
- Modify: `backend/app/ingestion/run.py` (mode `annuaires` + `--annuaire cfai|ufdi`)
- Modify: `backend/app/main.py` (endpoint dev `run-annuaires`)
- Modify: `backend/app/routes/opportunities.py` (le filtre `source` accepte déjà `annuaire`/`jeunes_studios` — vérifier ; sinon exposer dans meta)
- Modify: `frontend/lib/labels.ts` (libellés source `annuaire`/`jeunes_studios`), `frontend/components/Badges.tsx` (SourceBadge), `frontend/app/opportunities/page.tsx` (option filtre source)
- Create: `backend/tests/test_match_architecte.py`
- Create: `backend/tests/test_run_annuaires.py`

**Interfaces:**
- `naf_classifier.classify_naf_prescripteur(naf) -> bool` : `True` pour `71.11Z`/`74.10Z` (normalisés). ADDITIF, ne touche PAS `classify_naf`.
- `siret_matcher.match_architecte(name, city=None, postal=None, website=None, context=None, fetch=_http_get, llm_client=_USE_ENV) -> Optional[MatchResult]` : chemin **parallèle** à `match()`. (1) `search_by_name(name, city, postal, fetch)` ; (2) sélection par nom archi-gated (`classify_naf_prescripteur` au lieu de `classify_naf`) + géo cohérente → auto-accept `confidence="haute"`, `method="nom"` ; **corroboration domaine** : si `website` fourni et son domaine (hors `www`) apparaît dans le nom légal/enseignes d'un candidat archi, auto-accept même sans géo (`method="site"`) ; (3) pool résiduel archi → `arbitrate` (réutilisé) → `method="arbitre"`. **PAS d'étage adresse/near_point** (studios = bureaux à domicile, faible valeur ; évite aussi le filtre section "I" CHR). Le `match()` CHR n'est **jamais** modifié.
- `siret_matcher.dirigeant_from_result(data) -> Optional[str]` : premier dirigeant nommé de la charge utile `recherche-entreprises` (`data["dirigeants"][0]` → « Prénom Nom »), ou None. PURE.
- `pipeline.run_annuaires(annuaire, limit=800, max_pages=60, session=None, http_fetch=None, matcher=match_architecte, sirene=None) -> IngestStats` : orchestration miroir de `run_prescripteurs`. Connecteur (`CfaiConnector`/`UfdiConnector`, `http_fetch` injectable) → `to_candidates` → pour chaque candidat : **enrichissement SIREN archi** (`matcher(name=establishment_name, city, postal, website)` ; si SIREN trouvé → `sirene.lookup(siren)` pour `dirigeant_from_result` + `date_creation`→`activity_start_date`) → **fusion douce nom+ville** (`_soft_dedup_architecte`) → `_process_candidate`. `stats = IngestStats(source="annuaire", mode="annuaires")`. Commit par candidat (isolation). Fail-soft.
- `pipeline._soft_dedup_architecte(session, cand) -> Optional[Opportunity]` : cherche une Opportunity `population='architecte'`, `source != cand.source`, même nom normalisé (`_tokens` de `siret_matcher`) ET même ville normalisée. Le nom+ville identique est **NÉCESSAIRE mais PAS SUFFISANT** : il faut EN PLUS **au moins un signal de corroboration** entre le lead entrant et la fiche (même domaine de site, même code postal, ou même dirigeant normalisé) — sans quoi deux studios DIFFÉRENTS au même nom+ville (homonyme fortuit) seraient fusionnés à tort. **Exactement 1 candidat corroboré** → la renvoie (à fusionner) ; **0, ≥2, ou aucune corroboration** → None (create/skip — jamais de faux merge). PURE-ish (lit la DB, aucun réseau).
- `_process_candidate` (2 ajouts ciblés, sous garde `cand.source in ("annuaire",)` / architecte) :
  1. **Téléphone annuaire** : à la création et à l'upsert, si `cand.raw.get("phone")` et pas de `siret` phone, poser `opp.phone = cand.raw["phone"]` (UFDI expose le tél en clair).
  2. **Fusion nom+ville** : après l'échec de la fusion SIREN existante, si `cand.source == "annuaire"` et `_soft_dedup_architecte` renvoie une fiche, recopier le téléphone annuaire (`fiche.phone = fiche.phone or cand.raw.get("phone")`, sinon le tél UFDI serait perdu dans le cas fusion), appeler `_merge_corroboration(session, fiche, cand)` (fusion de trous + rescore), **enregistrer la paire fusionnée** `stats.soft_merges.append((cand.source_ref, fiche.source_ref))` (alimente le gate 0 faux merge de T5), puis `stats.updated += 1 ; return`.
- `pipeline.IngestStats` : nouveau champ `soft_merges: List[Tuple[str, str]] = field(default_factory=list)` — paires `(ref_annuaire, ref_insta)` EFFECTIVEMENT fusionnées par la voie douce, exposées via `run_annuaires` pour que l'éval T5 mesure le gate sur des fusions RÉELLES (et non sur une entrée fabriquée).
- `pipeline._corroborates(cand, opp) -> bool` (PURE-ish) : `True` si au moins un signal commun entre le lead et la fiche — même domaine de site (`_domain` de `siret_matcher`), même code postal (extrait de `address`), ou même dirigeant normalisé. Utilisé par `_soft_dedup_architecte`.
- `pipeline._merge_corroboration` (garde ADDITIVE, finding revue) : le tag score-bearing `CORROBORATION_TAG` (« corroboré registre × instagram », +1) ne doit être posé que pour une vraie fusion registre×instagram — **exclure la source `annuaire`** (un annuaire n'est pas un registre, et le label serait sémantiquement faux) : condition `if "instagram" in (opp.source, cand.source) and "annuaire" not in (opp.source, cand.source) and CORROBORATION_TAG not in sigs`. Le comportement CHR/A1 (aucune source `annuaire`) est inchangé.
- `CONNECTORS` += `{"cfai": CfaiConnector, "ufdi": UfdiConnector, "jeunes_studios": JeunesStudiosConnector}` (le délta passe par `run_ingestion(source="jeunes_studios")`). `SOURCE_LABELS` += `{"annuaire": "Annuaire", "jeunes_studios": "Sirene (jeunes studios)", "cfai": "CFAI", "ufdi": "UFDI"}`.
- CLI : `python -m app.ingestion.run --mode annuaires --annuaire cfai --limit 200` ; le délta : `python -m app.ingestion.run --mode window --source jeunes_studios --since 30 --limit 500`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_match_architecte.py
"""Matcher architecte (A2, T4) — chemin PARALLÈLE, aucun réseau (fetch injecté).
Le match() CHR n'est jamais sollicité ici (match_eval reste 8/9)."""
from app.ingestion.enrichment.naf_classifier import classify_naf_prescripteur
from app.ingestion.enrichment.siret_matcher import (
    dirigeant_from_result, match_architecte,
)


def test_classify_naf_prescripteur():
    assert classify_naf_prescripteur("71.11Z")
    assert classify_naf_prescripteur("74.10Z")
    assert not classify_naf_prescripteur("56.10A")  # CHR, pas archi
    assert not classify_naf_prescripteur(None)


def _search_payload(siren, nom, naf, cp, adresse, enseignes=None):
    return {"results": [{"siren": siren, "nom_complet": nom,
                         "activite_principale": naf,
                         "siege": {"siret": siren + "00011", "code_postal": cp,
                                   "adresse": adresse, "activite_principale": naf,
                                   "liste_enseignes": enseignes or []}}]}


def test_match_by_name_archi_naf_geo_consistent():
    def fetch(url, params):
        return _search_payload("500600700", "MANOA DESIGN", "71.11Z",
                               "75011", "10 RUE OBERKAMPF 75011 PARIS")
    got = match_architecte("Manoa Design", city="Paris", postal="75011", fetch=fetch)
    assert got is not None and got.siren == "500600700"
    assert got.method == "nom" and got.confidence == "haute"


def test_match_by_website_domain_corroboration_without_geo():
    # Domaine du site présent dans le nom légal -> auto-accept sans géo.
    def fetch(url, params):
        return _search_payload("111222333", "KOKOCINSKI STUDIO", "71.11Z",
                               "75007", "PARIS", enseignes=["Cecile Kokocinski"])
    got = match_architecte("Cecile Kokocinski Studio", city=None, postal=None,
                           website="https://www.cecilekokocinski.fr", fetch=fetch)
    assert got is not None and got.method == "site" and got.siren == "111222333"


def test_match_no_archi_candidate_returns_none():
    # Seul candidat en NAF CHR -> archi-gate le rejette, pas de merge nom-seul.
    def fetch(url, params):
        return _search_payload("999", "AUREA", "56.10A", "06590", "THEOULE")
    assert match_architecte("Aurea", city="Paris", postal="75001", fetch=fetch) is None


def test_dirigeant_from_result():
    data = {"dirigeants": [{"prenoms": "Cécile", "nom": "Kokocinski"}]}
    assert dirigeant_from_result(data) == "Cécile Kokocinski"
    assert dirigeant_from_result({"dirigeants": []}) is None
    assert dirigeant_from_result({}) is None
```

```python
# backend/tests/test_run_annuaires.py
"""run_annuaires (A2, T4) — orchestration sans réseau (http_fetch + matcher +
sirene injectés). Vérifie enrichissement SIREN, dédup nom+ville (0 faux merge)."""
from datetime import date

from sqlmodel import Session, SQLModel, create_engine, select

from app.ingestion.base import LeadCandidate
from app.ingestion.enrichment.siret_matcher import MatchResult
from app.ingestion.pipeline import (
    IngestStats, _process_candidate, _soft_dedup_architecte, run_annuaires,
)
from app.models import Opportunity


def _engine():
    e = create_engine("sqlite://")
    SQLModel.metadata.create_all(e)
    return e


CFAI_LIST = """<table class="table-list"><tbody>
<tr><td>75015</td><td>PARIS</td><td><b>ALEZRA Franck</b></td>
<td>SARL METROPOLE CONCEPT</td><td><a href="/annuaire-professionnel/adherent/12"></a></td></tr>
</tbody></table><span class="badge bg-secondary">1 résultats</span>"""
CFAI_FICHE = """<header><h1>Franck ALEZRA</h1>
<p class="member-company">SARL METROPOLE CONCEPT</p></header>
<h3>Adresse</h3><div class="details-group">13 rue Mademoiselle 75015 PARIS</div>
<h3>Site</h3><div class="details-group">
<a href="http://www.metropole-concept.com">site</a></div>"""


def test_run_annuaires_enriches_siren_and_dirigeant(monkeypatch):
    pages = {
        "https://www.cfai.fr/fr/recherche/annuaire-professionnel?page=1": CFAI_LIST,
        "https://www.cfai.fr/annuaire-professionnel/adherent/12": CFAI_FICHE,
    }

    def matcher(name, city=None, postal=None, website=None, context=None, **k):
        return MatchResult(siren="500600700", siret="50060070000011", naf="71.11Z",
                           enseigne="METROPOLE CONCEPT", confidence="haute",
                           method="nom", date_creation="2015-03-01")

    class _Sirene:
        def lookup(self, siren):
            return {"dirigeants": [{"prenoms": "Franck", "nom": "Alezra"}],
                    "siege": {"date_creation": "2015-03-01"}}

    with Session(_engine()) as s:
        stats = run_annuaires("cfai", limit=10, session=s,
                              http_fetch=lambda u: pages.get(u),
                              matcher=matcher, sirene=_Sirene())
        assert stats.created == 1
        opp = s.exec(select(Opportunity).where(Opportunity.source == "annuaire")).first()
        assert opp is not None
        assert opp.population == "architecte" and opp.siren == "500600700"
        assert opp.decision_maker == "Franck Alezra"
        assert opp.activity_start_date == date(2015, 3, 1)
        assert opp.lifecycle_label == "studio_actif"


def test_soft_dedup_exact_one_match_with_corroboration():
    with Session(_engine()) as s:
        # Studio Insta existant (source instagram, sans SIREN) AVEC un site.
        _process_candidate(s, LeadCandidate(
            source="instagram", source_ref="metropole_concept",
            establishment_name="Metropole Concept", city="Paris", address="",
            website="http://www.metropole-concept.com",
            main_signal="prescripteur actif", detection_date=date(2026, 7, 11),
            establishment_type="architecte d'intérieur", population="architecte"),
            IngestStats(source="instagram"), set(), None)
        s.commit()
        # Même nom+ville ET même domaine de site -> corroboration OK.
        incoming = LeadCandidate(
            source="annuaire", source_ref="cfai:12",
            establishment_name="Metropole Concept", city="Paris", address="",
            website="https://metropole-concept.com",
            main_signal="prescripteur actif", detection_date=date(2026, 7, 11),
            establishment_type="architecte d'intérieur", population="architecte")
        match = _soft_dedup_architecte(s, incoming)
        assert match is not None and match.source == "instagram"


def test_soft_dedup_name_city_only_without_corroboration_returns_none():
    # Homonyme fortuit : même nom+ville mais AUCUN signal commun -> pas de merge
    # (nom+ville nécessaire mais pas suffisant, finding revue).
    with Session(_engine()) as s:
        _process_candidate(s, LeadCandidate(
            source="instagram", source_ref="studio_x_insta",
            establishment_name="Studio X", city="Paris", address="",
            main_signal="prescripteur actif", detection_date=date(2026, 7, 11),
            establishment_type="architecte d'intérieur", population="architecte"),
            IngestStats(source="instagram"), set(), None)
        s.commit()
        incoming = LeadCandidate(
            source="annuaire", source_ref="cfai:77",
            establishment_name="Studio X", city="Paris", address="",
            website="https://un-autre-studio-x.fr",
            main_signal="prescripteur actif", detection_date=date(2026, 7, 11),
            establishment_type="architecte d'intérieur", population="architecte")
        assert _soft_dedup_architecte(s, incoming) is None


def test_soft_dedup_two_matches_returns_none_no_false_merge():
    with Session(_engine()) as s:
        for ref in ("a", "b"):
            _process_candidate(s, LeadCandidate(
                source="instagram", source_ref=ref, establishment_name="Atelier Design",
                city="Lyon", address="", main_signal="prescripteur actif",
                detection_date=date(2026, 7, 11),
                establishment_type="architecte d'intérieur", population="architecte"),
                IngestStats(source="instagram"), set(), None)
        s.commit()
        incoming = LeadCandidate(
            source="annuaire", source_ref="cfai:99", establishment_name="Atelier Design",
            city="Lyon", address="", main_signal="prescripteur actif",
            detection_date=date(2026, 7, 11),
            establishment_type="architecte d'intérieur", population="architecte")
        # 2 homonymes -> pas de merge (vide/2 fiches > faux merge).
        assert _soft_dedup_architecte(s, incoming) is None


def test_annuaire_incoming_merges_into_insta_by_name_city():
    with Session(_engine()) as s:
        # Fiche Insta existante avec un code postal (support de corroboration).
        _process_candidate(s, LeadCandidate(
            source="instagram", source_ref="studio_lumen",
            establishment_name="Studio Lumen", city="Bordeaux",
            address="12 rue X 33000 Bordeaux",
            main_signal="prescripteur actif", detection_date=date(2026, 7, 11),
            establishment_type="architecte d'intérieur", population="architecte"),
            IngestStats(source="instagram"), set(), None)
        s.commit()
        stats = IngestStats(source="annuaire")
        # Même nom+ville ET même code postal -> corroboration OK ; le site annuaire
        # comble un trou.
        _process_candidate(s, LeadCandidate(
            source="annuaire", source_ref="ufdi:studio-lumen-1",
            establishment_name="Studio Lumen", city="Bordeaux",
            address="5 avenue Y 33000 Bordeaux",
            main_signal="prescripteur actif", detection_date=date(2026, 7, 11),
            establishment_type="architecte d'intérieur", population="architecte",
            website="https://studiolumen.fr"),
            stats, set(), None)
        s.commit()
        # Une seule fiche (fusion), enrichie du site annuaire.
        rows = s.exec(select(Opportunity).where(
            Opportunity.population == "architecte")).all()
        assert len(rows) == 1
        assert rows[0].website == "https://studiolumen.fr"
        assert stats.updated == 1
        # La paire fusionnée est tracée (alimente le gate 0 faux merge, T5).
        assert stats.soft_merges == [("ufdi:studio-lumen-1", "studio_lumen")]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_match_architecte.py tests/test_run_annuaires.py -q`
Expected: FAIL — `ImportError` (`match_architecte`/`classify_naf_prescripteur`/`run_annuaires`/`_soft_dedup_architecte` absents).

- [ ] **Step 3: Write the implementation**

**a) `naf_classifier.py`** — ajouter après `classify_naf` :

```python
# Codes NAF PRESCRIPTEUR (archi d'intérieur / design) — gate du matcher architecte
# (A2). SÉPARÉ de classify_naf (CHR) : le matcher CHR reste bit-à-bit intact.
NAF_PRESCRIPTEUR = {"71.11Z", "74.10Z"}


def classify_naf_prescripteur(naf: Optional[str]) -> bool:
    """True si le NAF est un code d'architecture d'intérieur / design (A2)."""
    return _normalize_naf(naf) in NAF_PRESCRIPTEUR
```

**b) `siret_matcher.py`** — ajouter (le `match()` CHR reste inchangé) :

```python
from .naf_classifier import classify_naf, classify_naf_prescripteur  # (compléter l'import existant)


def _domain(url: Optional[str]) -> str:
    """URL -> nom de domaine nu (sans www ni TLD), pour la corroboration site."""
    if not url:
        return ""
    m = re.search(r"https?://(?:www\.)?([^/]+)", url)
    host = (m.group(1) if m else url).split("/")[0]
    core = host.split(".")[0]  # 'cecilekokocinski' de 'cecilekokocinski.fr'
    return re.sub(r"[^a-z0-9]", "", core.lower())


def _pick_archi_by_name(cands: List[Dict[str, Any]], name: str,
                        city: Optional[str], postal: Optional[str],
                        website: Optional[str]) -> Tuple[Optional[Dict[str, Any]], str]:
    """Sélection archi PURE : NAF archi + token distinctif commun. Auto-accept si
    géo cohérente (method 'nom') OU domaine du site présent dans le nom/enseignes
    (method 'site'). -> (candidat|None, method)."""
    dom = _domain(website)
    for cand in cands:
        if not classify_naf_prescripteur(cand["naf"]):
            continue
        legal = f'{cand["nom"]} {cand["enseignes"]}'
        if not _name_overlap(name, legal):
            continue
        if _geo_consistent(cand, city, postal):
            return cand, "nom"
        if dom and dom in re.sub(r"[^a-z0-9]", "", legal.lower()):
            return cand, "site"
    return None, ""


def dirigeant_from_result(data: Dict[str, Any]) -> Optional[str]:
    """Premier dirigeant nommé de la charge utile recherche-entreprises. PURE."""
    for d in (data.get("dirigeants") or []):
        prenom = (d.get("prenoms") or d.get("prenom") or "").strip()
        nom = (d.get("nom") or "").strip()
        full = " ".join(p for p in (prenom, nom) if p).strip()
        if full:
            return full
    return None


def match_architecte(name: str, city: Optional[str] = None, postal: Optional[str] = None,
                     website: Optional[str] = None, context: Optional[str] = None,
                     fetch: Fetch = _http_get, llm_client=_USE_ENV) -> Optional[MatchResult]:
    """Matching SIREN d'un studio d'archi d'intérieur (A2). Chemin PARALLÈLE au
    match() CHR : gate NAF 71.11Z/74.10Z, nom+ville+domaine, PAS d'étage adresse
    (studios = bureaux à domicile). Fail-soft None (le lead vit sans SIREN)."""
    if not name:
        return None
    cands = search_by_name(name, city, postal, fetch)
    got, method = _pick_archi_by_name(cands, name, city, postal, website)
    if got:
        return _result(got, "haute" if method == "nom" else "moyenne", method)
    pool = [c for c in cands
            if classify_naf_prescripteur(c["naf"])
            and _name_overlap(name, f'{c["nom"]} {c["enseignes"]}')]
    if pool:
        uniq = list({c["siren"]: c for c in pool}.values())
        client = _openai_client() if llm_client is _USE_ENV else llm_client
        siren = arbitrate(name, context, uniq, client)
        if siren:
            cand = next(c for c in uniq if c["siren"] == siren)
            return _result(cand, "moyenne", "arbitre")
    return None
```

**c) `pipeline.py`** :

Imports (en tête, à côté des imports d'ingestion existants) :

```python
from .annuaires.cfai import CfaiConnector
from .annuaires.ufdi import UfdiConnector
from .jeunes_studios import JeunesStudiosConnector
from .enrichment.siret_matcher import match_architecte, dirigeant_from_result
```

`CONNECTORS` et `SOURCE_LABELS` :

```python
CONNECTORS = {
    "bodacc": BodaccConnector,
    "sirene": SireneDeltaConnector,
    "jeunes_studios": JeunesStudiosConnector,
    "cfai": CfaiConnector,
    "ufdi": UfdiConnector,
}
```

```python
SOURCE_LABELS = {
    "bodacc": "BODACC", "instagram": "Instagram", "sirene": "Sirene (délta)",
    "annuaire": "Annuaire", "jeunes_studios": "Sirene (jeunes studios)",
    "cfai": "CFAI", "ufdi": "UFDI",
}
```

**`IngestStats`** — ajouter le champ (nécessite `from dataclasses import field` et `Tuple` de `typing`, déjà importés le cas échéant) :

```python
    # Paires (ref_annuaire, ref_insta) fusionnées par la voie douce nom+ville [A2].
    # Exposées pour que l'éval T5 mesure le gate 0 faux merge sur des fusions RÉELLES.
    soft_merges: List[Tuple[str, str]] = field(default_factory=list)
```

**`_merge_corroboration`** — resserrer la garde du tag score-bearing (finding revue) pour exclure la source `annuaire` (un annuaire n'est pas un registre ; le libellé « corroboré registre × instagram » serait sémantiquement faux ET fausserait le score archi via la famille +1) :

```python
    # AVANT : if "instagram" in (opp.source, cand.source) and CORROBORATION_TAG not in sigs:
    if ("instagram" in (opp.source, cand.source)
            and "annuaire" not in (opp.source, cand.source)
            and CORROBORATION_TAG not in sigs):
        sigs.append(CORROBORATION_TAG)
```

(CHR/A1 n'ont jamais de source `annuaire` -> comportement bit-à-bit inchangé pour eux.)

`_soft_dedup_architecte` (nouvelle fonction, après `_source_cursor`) :

```python
def _postal(text: Optional[str]) -> str:
    """Premier code postal à 5 chiffres d'un texte, ou '' (helper corroboration)."""
    m = re.search(r"\b(\d{5})\b", text or "")
    return m.group(1) if m else ""


def _corroborates(cand: LeadCandidate, opp: Opportunity) -> bool:
    """Au moins UN signal secondaire commun entre le lead annuaire et la fiche
    existante : même domaine de site, même code postal, ou même dirigeant
    normalisé. Garde-fou anti-homonyme fortuit de la fusion douce (A2). PURE-ish."""
    from .enrichment.siret_matcher import _domain
    dc, do = _domain(cand.website), _domain(opp.website)
    if dc and dc == do:
        return True
    pc, po = _postal(cand.address), _postal(opp.address)
    if pc and pc == po:
        return True

    def _dm(x: Optional[str]) -> str:
        return re.sub(r"[^a-z0-9]", "", (x or "").lower())

    if cand.decision_maker and opp.decision_maker and _dm(cand.decision_maker) == _dm(opp.decision_maker):
        return True
    return False


def _soft_dedup_architecte(session: Session, cand: LeadCandidate) -> Optional[Opportunity]:
    """Fusion douce nom+ville pour la population architecte (A2). Cherche une
    Opportunity architecte d'une AUTRE source, même nom normalisé ET même ville
    normalisée. Le nom+ville est NÉCESSAIRE mais PAS SUFFISANT : il faut AUSSI une
    corroboration (`_corroborates`) pour écarter l'homonyme fortuit (deux studios
    distincts au même nom+ville). Exactement 1 candidat corroboré -> la renvoie ;
    0, >=2, ou aucune corroboration -> None (jamais de faux merge : vide/2 fiches >
    mauvaise fusion). DB seule, aucun réseau."""
    from .enrichment.siret_matcher import _tokens, _city_tokens
    want_name = _tokens(cand.establishment_name)
    want_city = _city_tokens(cand.city)
    if not want_name or not want_city:
        return None
    rows = session.exec(
        select(Opportunity).where(
            Opportunity.population == "architecte",
            Opportunity.source != cand.source,
        )
    ).all()
    hits = [o for o in rows
            if _tokens(o.establishment_name) == want_name
            and _city_tokens(o.city) == want_city]
    if len(hits) != 1:
        return None
    return hits[0] if _corroborates(cand, hits[0]) else None
```

Dans `_process_candidate`, (1) **téléphone annuaire** — dans la branche création, après avoir construit `opp` mais avant `session.add(opp)` (ou juste poser `phone=` dans le constructeur), ajouter :

```python
    # Téléphone exposé en clair par l'annuaire (UFDI data-numero) -> Opportunity.phone.
    if cand.source == "annuaire" and cand.raw.get("phone"):
        opp.phone = cand.raw["phone"]
```

et symétriquement dans la branche `if existing:` (upsert même-source), après le bloc contact :

```python
    if cand.source == "annuaire" and cand.raw.get("phone"):
        existing.phone = existing.phone or cand.raw["phone"]
```

(2) **Fusion nom+ville** — dans `_process_candidate`, juste APRÈS le bloc de fusion SIREN (`if corroborated is not None: ...`) et AVANT le bloc `if existing:`, insérer :

```python
    # FUSION DOUCE NOM+VILLE [A2] : un lead ANNUAIRE entrant qui désigne le même
    # studio qu'une fiche Instagram/délta existante (dont les studios Insta n'ont
    # PAS de SIREN -> pas de fusion SIREN possible) est réconcilié par nom+ville.
    # Asymétrique (seul l'annuaire entrant déclenche) -> run_prescripteurs (A1)
    # bit-à-bit identique. Conservateur : exactement 1 fiche sinon rien.
    if existing is None and corroborated is None and cand.source == "annuaire":
        soft = _soft_dedup_architecte(session, cand)
        if soft is not None:
            label = SOURCE_LABELS.get(cand.source, cand.source)
            already = session.exec(
                select(Signal).where(
                    Signal.opportunity_id == soft.id,
                    Signal.source == label,
                    Signal.signal_type == cand.main_signal,
                )
            ).first()
            if already is not None:
                return
            # Téléphone UFDI (data-numero) : sans ceci, il serait perdu dans le
            # cas fusion (_merge_corroboration ne touche pas au phone).
            if cand.raw.get("phone"):
                soft.phone = soft.phone or cand.raw["phone"]
            _merge_corroboration(session, soft, cand)
            # Trace de la fusion réelle -> alimente le gate 0 faux merge (T5).
            stats.soft_merges.append((cand.source_ref, soft.source_ref))
            stats.updated += 1
            return
```

> **Note** : `_soft_dedup_architecte` est appelé après l'échec des deux voies SIREN (`existing`/`corroborated`), donc uniquement quand aucune identité forte n'a réconcilié le lead. `_merge_corroboration` remplit les trous (site, instagram, dirigeant…) sans écraser, tague et rescore.

`run_annuaires` (nouvelle orchestration, après `run_prescripteurs`) :

```python
ANNUAIRE_CONNECTORS = {"cfai": CfaiConnector, "ufdi": UfdiConnector}


def run_annuaires(
    annuaire: str = "cfai",
    limit: int = 800,
    max_pages: int = 60,
    session: Optional[Session] = None,
    http_fetch=None,
    matcher=match_architecte,
    sirene: Optional[SireneEnricher] = None,
) -> IngestStats:
    """Source STOCK ANNUAIRE (A2) : CFAI/UFDI -> enrichissement SIREN archi
    (dirigeant + ancienneté) -> fusion douce nom+ville -> pipeline existant
    (branche population='architecte' de A1). Upsert source='annuaire', dédup par
    source_ref (cfai:<id> / ufdi:<slug>). MIROIR de run_prescripteurs. Fail-soft ;
    commit par candidat (isolation). `http_fetch`/`matcher`/`sirene` injectables."""
    init_db()
    if annuaire not in ANNUAIRE_CONNECTORS:
        raise ValueError(f"Annuaire inconnu : {annuaire}. Choix : {list(ANNUAIRE_CONNECTORS)}")
    own_session = session is None
    session = session or Session(engine)
    stats = IngestStats(source="annuaire", mode="annuaires")
    sirene = sirene or SireneEnricher()

    try:
        connector = (ANNUAIRE_CONNECTORS[annuaire](http_fetch=http_fetch)
                     if http_fetch is not None else ANNUAIRE_CONNECTORS[annuaire]())
        records = connector.fetch(limit=limit, max_pages=max_pages)
        stats.fetched = len(records)
        stats.total_available = getattr(connector, "last_total_count", 0) or 0
        stats.truncated = stats.total_available > stats.fetched
        candidates = connector.to_candidates(records)
        seen_refs: set = set()

        for cand in candidates:
            try:
                # Enrichissement SIREN archi (dirigeant + ancienneté) — fail-soft.
                postal = None
                m = re.search(r"\b(\d{5})\b", cand.address or "")
                if m:
                    postal = m.group(1)
                mr = matcher(name=cand.establishment_name, city=cand.city,
                             postal=postal, website=cand.website)
                if mr and mr.siren:
                    cand.siren = mr.siren
                    cand.siret = cand.siret or mr.siret
                    cand.naf = cand.naf or mr.naf
                    cand.siren_match_method = mr.method
                    cand.siren_match_confidence = mr.confidence
                    data = sirene.lookup(mr.siren)
                    if data:
                        dm = dirigeant_from_result(data)
                        if dm and not cand.decision_maker:
                            cand.decision_maker = dm
                        created = _ymd((data.get("siege") or {}).get("date_creation")
                                       or data.get("date_creation")
                                       or mr.date_creation)
                        cand.activity_start_date = cand.activity_start_date or created
                _process_candidate(session, cand, stats, seen_refs, enricher=None)
                session.commit()
            except Exception:
                stats.errors += 1
                session.rollback()
        session.commit()
    finally:
        if own_session:
            session.close()

    return stats
```

> **Cohérence** : `_process_candidate` reçoit `enricher=None` (l'enrichissement SIREN archi est déjà fait en amont ; la branche `is_architecte` de A1 saute de toute façon l'enricher CHR). Les leads annuaire portent maintenant leur SIREN → si ce SIREN correspond à une fiche délta (`jeunes_studios`) déjà en base, la **fusion SIREN existante** (`corroborated`) les réconcilie automatiquement.

> **Pas de verdict caché, pas d'auto-purge (documenté)** : `run_annuaires` contourne volontairement `verdict_cache`/`PRESCRIBER_ROUTING` (le connecteur pose `lifecycle_label='studio_actif'`/`main_signal='prescripteur actif'` directement — source de confiance). Un lead annuaire ne cache donc **aucun** verdict. Conséquence assumée : les fiches `source='annuaire'` ne passent JAMAIS par `_purge_requalified` → un membre retiré ensuite du CFAI/UFDI **n'est pas désactivé automatiquement** (asymétrie avec la requalification Instagram). Acceptable en A2 (stock stable, faible churn) ; un **balayage stale-annuaire** (revisite périodique + désactivation des refs disparues) est renvoyé à **A3** (cf. « Hors périmètre A2 »).

**d) `run.py`** — importer `run_annuaires`, ajouter le mode et l'argument :

```python
from .pipeline import (
    ..., run_annuaires, ...
)
```

Ajouter `"annuaires"` aux `choices`, l'argument `--annuaire`, et le dispatch :

```python
    parser.add_argument("--annuaire", default="cfai", choices=["cfai", "ufdi"],
                        help="Annuaire à crawler (mode annuaires).")
```

```python
    elif args.mode == "annuaires":
        stats = run_annuaires(annuaire=args.annuaire, limit=args.limit)
```

Compléter la docstring des modes :

```
  annuaires      population architectes (A2) : stock CFAI/UFDI (--annuaire cfai|ufdi)
```

et l'exemple :

```
    python -m app.ingestion.run --mode annuaires --annuaire cfai --limit 200
    python -m app.ingestion.run --mode window --source jeunes_studios --since 30 --limit 500
```

**e) `main.py`** — endpoint dev (après `run_prescripteurs_endpoint`) :

```python
@dev_router.post("/run-annuaires")
def run_annuaires_endpoint(annuaire: str = "cfai", limit: int = 400):
    from .ingestion.pipeline import run_annuaires, stats_to_dict
    try:
        return stats_to_dict(run_annuaires(annuaire=annuaire, limit=limit))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc))
```

**f) Frontend** — `frontend/lib/labels.ts` : étendre `SOURCE_LABELS`/`SOURCE_STYLES` (si présents) avec `annuaire: "Annuaire"` et `jeunes_studios: "Jeune studio"` ; `frontend/components/Badges.tsx` (`SourceBadge`) gère déjà un fallback — vérifier que les nouvelles clés s'affichent ; `frontend/app/opportunities/page.tsx` : ajouter les options au filtre source :

```tsx
              <option value="annuaire">Annuaire</option>
              <option value="jeunes_studios">Jeunes studios</option>
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_match_architecte.py tests/test_run_annuaires.py -q` → PASS.
Run: `python -m pytest tests/ -q` → **tout vert** (le `match()` CHR n'est pas touché ; la fusion nom+ville ne se déclenche que pour `cand.source=='annuaire'` → aucun test CHR/A1 impacté).
Run (non-régression matching, obligatoire) : `python -m app.ingestion.eval.match_eval` → **8/9, 0 faux merge**.

- [ ] **Step 5: Commit**

```bash
git add backend/app/ingestion/enrichment/naf_classifier.py backend/app/ingestion/enrichment/siret_matcher.py backend/app/ingestion/pipeline.py backend/app/ingestion/run.py backend/app/main.py backend/tests/test_match_architecte.py backend/tests/test_run_annuaires.py frontend/lib/labels.ts frontend/components/Badges.tsx frontend/app/opportunities/page.tsx
git commit -m "feat(annuaires): matcher architecte + enrichissement SIREN + run_annuaires + dedup nom+ville + CONNECTORS/CLI/UI"
```

---

### Task 5: Éval élargie (annuaire/délta) + gate 0 faux merge + run réel borné + docs

**Modèle d'exécution recommandé : opus**

**Files:**
- Modify: `backend/app/ingestion/eval/architectes_groundtruth.csv` (+8-10 cas annuaire/délta annotés)
- Create: `backend/app/ingestion/eval/annuaires_snapshots/` (fixtures HTML minimales cfai/ufdi + un record INSEE JSON de délta)
- Modify: `backend/app/ingestion/eval/prescripteurs_metrics.py` (`false_merges_annuaire_insta`)
- Modify: `backend/app/ingestion/eval/prescripteurs_run.py` (gate `0 faux merge`, section annuaires)
- Create: `backend/tests/test_annuaires_eval.py`
- Create: `docs/population-architectes-design.md` **ou** `docs/a2-annuaires-design.md` (décisions A2, sondes, périmètre, Houzz reporté)
- Modify: `C:\Users\Alexis\.claude\projects\c--Users-Alexis-Documents-Projets\memory\MEMORY.md` (index + note A2)

**Interfaces:**
- **GT élargi** : le CSV A1 existant (`architectes_groundtruth.csv`, colonnes `handle,name,label,confidence,provenance,rationale,annotated_at`) reçoit ~8-10 lignes annuaire/délta réelles (annotées pendant/après le run), `provenance=annuaire_cfai|annuaire_ufdi|delta_insee`. Le `handle` devient l'identifiant lead : `cfai:<id>`, `ufdi:<slug>`, `siret:<siret>`. `label ∈ {studio_actif, hors_cible, ...}` (les membres CFAI/UFDI = `studio_actif` par construction ; un faux positif délta = `hors_cible`).
- `prescripteurs_metrics.false_merges_annuaire_insta(pairs, truth_same_studio) -> List[Tuple[str, str]]` (PURE) : reçoit les paires `(ref_annuaire, ref_insta)` **effectivement fusionnées par le pipeline** (`stats.soft_merges`, cf. T4) + l'ensemble `truth_same_studio` des paires annotées comme le MÊME studio, et renvoie les fusions NON justifiées (studios différents). Gate : **liste vide**.
- `prescripteurs_run.py` : ajouter une section « annuaires » qui, sur un mini-jeu offline **livré** (fixtures HTML de `annuaires_snapshots/` + fiches Insta pré-semées en DB mémoire), fait tourner `run_annuaires` avec un `matcher`/`sirene` déterministes injectés, **récupère les paires réellement fusionnées `stats.soft_merges`** et les passe à `false_merges_annuaire_insta(stats.soft_merges, truth_same_studio)` (où `truth_same_studio` est l'ensemble annoté des paires « même studio » des fixtures). Le mini-jeu est construit pour **exercer réellement la métrique** : il sème (a) un couple annuaire×insta LÉGITIME (même studio, corroboré → fusion attendue, NE DOIT PAS être flaggé) et (b) un homonyme DISTINCT même nom+ville sans corroboration (NE DOIT PAS fusionner → `soft_merges` ne le contient pas). Gate `GATE_ZERO_FALSE_MERGE` = liste vide. Vérifie aussi **≥70 % des membres annuaire → studio_actif** (les honoraires CFAI écartés en amont ne comptent pas). Gate global A2 : `gates_pass = gate_studio_precision AND gate_zero_hors_cible_in_tiers AND gate_zero_false_merge`. **Le gate faux-merge tourne TOUJOURS sur les fixtures livrées — il n'est jamais court-circuité à `True` faute de données.**
- **Tests unitaires (T5) NE lancent NI LLM NI réseau** : fixtures HTML injectées, matcher/sirene factices. Le gate LLM live (arbitre) + le run réel borné sont manuels.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_annuaires_eval.py
"""Éval annuaires/délta (A2, T5) — offline. Gate 0 faux merge annuaire×insta."""
from datetime import date

from sqlmodel import Session, SQLModel, create_engine, select

from app.ingestion.base import LeadCandidate
from app.ingestion.eval.prescripteurs_metrics import false_merges_annuaire_insta
from app.ingestion.pipeline import IngestStats, _process_candidate, run_annuaires
from app.models import Opportunity


def _engine():
    e = create_engine("sqlite://")
    SQLModel.metadata.create_all(e)
    return e


def test_false_merges_metric_pure():
    # Vérité : (annuaire, insta) sont le MÊME studio -> pas un faux merge.
    truth_same = {("cfai:12", "metropole_concept")}
    pairs = [("cfai:12", "metropole_concept"), ("cfai:99", "autre_studio")]
    fm = false_merges_annuaire_insta(pairs, truth_same)
    assert fm == [("cfai:99", "autre_studio")]  # merge non justifié = faux merge


def test_run_annuaires_no_false_merge_on_distinct_homonym(monkeypatch):
    # Deux "Atelier Design" à Lyon (homonymes distincts) : l'annuaire NE fusionne
    # PAS (dédup renvoie None sur >=2) -> 0 faux merge, 2 fiches conservées.
    LIST = ('<table class="table-list"><tbody><tr><td>69001</td><td>LYON</td>'
            '<td><b>NOUVEAU Atelier</b></td><td>Atelier Design</td>'
            '<td><a href="/annuaire-professionnel/adherent/50"></a></td></tr>'
            '</tbody></table><span class="badge bg-secondary">1 résultats</span>')
    FICHE = ('<header><h1>Paul NOUVEAU</h1><p class="member-company">Atelier Design'
             '</p></header><h3>Adresse</h3><div class="details-group">1 rue X 69001 LYON</div>')
    pages = {"https://www.cfai.fr/fr/recherche/annuaire-professionnel?page=1": LIST,
             "https://www.cfai.fr/annuaire-professionnel/adherent/50": FICHE}
    with Session(_engine()) as s:
        for ref in ("insta_a", "insta_b"):
            _process_candidate(s, LeadCandidate(
                source="instagram", source_ref=ref, establishment_name="Atelier Design",
                city="Lyon", address="", main_signal="prescripteur actif",
                detection_date=date(2026, 7, 11),
                establishment_type="architecte d'intérieur", population="architecte"),
                IngestStats(source="instagram"), set(), None)
        s.commit()
        stats = run_annuaires("cfai", limit=10, session=s,
                              http_fetch=lambda u: pages.get(u),
                              matcher=lambda **k: None, sirene=_NoSirene())
        # 2 Insta + 1 annuaire = 3 fiches (aucune fusion abusive sur homonyme).
        rows = s.exec(select(Opportunity).where(
            Opportunity.population == "architecte")).all()
        assert len(rows) == 3
        # Aucune fusion émise -> le gate 0 faux merge est nourri du vrai signal.
        assert stats.soft_merges == []
        assert false_merges_annuaire_insta(stats.soft_merges, set()) == []


class _NoSirene:
    def lookup(self, siren):
        return None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_annuaires_eval.py -q`
Expected: FAIL — `ImportError: false_merges_annuaire_insta`.

- [ ] **Step 3: Write the implementation**

**a) `prescripteurs_metrics.py`** — ajouter :

```python
def false_merges_annuaire_insta(pairs, truth_same_studio) -> list:
    """Paires (ref_annuaire, ref_insta) EFFECTIVEMENT fusionnées par le pipeline
    -> celles NON justifiées par la vérité (studios différents) = FAUX MERGES.
    `truth_same_studio` : ensemble des paires annotées comme le MÊME studio.
    DOIT être vide (gate dur A2). PURE."""
    return [p for p in pairs if tuple(p) not in truth_same_studio]
```

**b) `prescripteurs_run.py`** — ajouter la constante `GATE_ZERO_FALSE_MERGE` et intégrer le gate au dict de sortie (`gates_pass = gate_studio_precision and gate_zero_hors_cible_in_tiers and gate_zero_false_merge`). Le gate tourne sur le **mini-jeu offline LIVRÉ** (fixtures `annuaires_snapshots/` + fiches Insta pré-semées) : la section fait tourner `run_annuaires` sur DB mémoire (`matcher`/`sirene` factices), **collecte `stats.soft_merges` (paires RÉELLEMENT fusionnées par le pipeline)** et calcule `false_merges_annuaire_insta(stats.soft_merges, truth_same_studio)` ; `gate_zero_false_merge = (résultat == [])`. **NE PAS court-circuiter le gate à `True`** : les fixtures sont committées avec T5 et exercent la métrique de bout en bout (un couple légitime à ne pas flagger + un homonyme distinct à ne pas fusionner). Documenter dans la docstring que le run réel + annotation navigateur ÉTENDENT le GT au-delà de ces fixtures, mais que le gate offline est autonome.

> **Note (câblage bout-en-bout)** : c'est `_process_candidate` (voie soft-merge, T4) qui émet chaque paire dans `stats.soft_merges` ; `run_annuaires` la propage dans les `IngestStats` qu'il retourne ; l'éval la consomme telle quelle. La métrique n'est donc plus une fonction pure jamais alimentée : elle voit les fusions effectives du pipeline sur snapshots.

**c) GT CSV** — après le run réel borné (Step 4), ajouter ~8-10 lignes annotées à `architectes_groundtruth.csv`, ex. (à remplacer par les cas RÉELS observés) :

```csv
cfai:12,Franck ALEZRA (Metropole Concept),studio_actif,high,annuaire_cfai,"membre CFAI, societe active, contact complet email/tel/site",2026-07-11
cfai:17,Francois ARNAUDEAU,hors_cible,high,annuaire_cfai,"Membre Honoraire du CFAI = retraite, ecarte par le garde parse_fiche",2026-07-11
ufdi:cecile-kokocinski,Cecile Kokocinski Studio,studio_actif,high,annuaire_ufdi,"membre UFDI, tags Decoration Hotels+Restaurants -> tier T2",2026-07-11
ufdi:delphine-benedetti,DBinteriors,studio_actif,med,annuaire_ufdi,"membre UFDI sans tag hospitality -> tier T3",2026-07-11
siret:99988877700022,MANOA DESIGN,studio_actif,med,delta_insee,"creation recente NAF 71.11Z, denomination qualifiante",2026-07-11
siret:XXXXXXXXX,LEA LAXTON DESIGN GRAPHIQUE,hors_cible,high,delta_insee,"NAF 74.10Z graphisme (garde negatif) = bruit adjacent, pas archi interieur",2026-07-11
```

**d) `annuaires_snapshots/`** — copier les extraits HTML minimaux utilisés par les tests (ou de vrais fichiers réduits des sondes) pour le déterminisme offline du gate faux-merge.

**e) `docs/a2-annuaires-design.md`** — rédiger : décisions produit (VOLUME MAX, annuaires = stock, délta = flux faible priorité) ; résumé des sondes (CFAI 738 statique + filtre honoraire ; UFDI ~157 profils statiques en un fetch — PAS 255, dont ~98 liens dept-nav exclus — + hospitality natif ; délta 54/j brut → ~5/j qualifiables, 65 % masqués) ; architecture (connecteurs + matcher archi parallèle + run_annuaires + dédup nom+ville) ; **Houzz REPORTÉ** (anti-bot dur, ToS incertain — cité verbatim de la sonde, à réévaluer via API partenaire) ; gates d'éval (studio_actif ≥ 70 %, 0 hors_cible en tiers, **0 faux merge annuaire×insta**) ; **Hors périmètre A2** (Houzz automatisé ; watchlist « nouveau projet » = A3 ; scheduling ; génération de messages).

**f) `MEMORY.md`** — ajouter à l'index : « A2 annuaires — plan `docs/plans/2026-07-11-a2-annuaires.md` : connecteurs CFAI/UFDI (source='annuaire') + délta jeunes studios (NAF archi) + matcher architecte + dédup nom+ville ; gates studio_actif≥70 % / 0 hors_cible en tiers / 0 faux merge ; Houzz reporté ».

- [ ] **Step 4: Run tests + gates + run réel borné**

Run: `python -m pytest tests/test_annuaires_eval.py -q` → PASS.
Run: `python -m pytest tests/ -q` → **tout vert**.
Run (non-régression, obligatoire) : `python -m app.ingestion.eval.match_eval` → **8/9, 0 faux merge**. Avec `OPENAI_API_KEY` : `python -m app.ingestion.eval.run` → gates CHR verts ; `python -m app.ingestion.eval.prescripteurs_run` → gates A1+A2 `OK` (studio_actif ≥ 70 %, 0 hors_cible en tiers, 0 faux merge).
**Run réel borné** (coût réseau assumé, scraping poli) :
- `python -m app.ingestion.run --mode annuaires --annuaire ufdi --limit 30` (UFDI d'abord : le plus simple, 1 page + 30 fiches ≈ 90 s throttle) → vérifier des leads `source=annuaire, population=architecte, lifecycle=studio_actif`, tiers T2 pour les fiches hospitality (`?population=architecte&source=annuaire` dans l'UI).
- `python -m app.ingestion.run --mode annuaires --annuaire cfai --limit 30` → idem, honoraires absents.
- Avec `INSEE_API_KEY` : `python -m app.ingestion.run --mode window --source jeunes_studios --since 30 --limit 100` → leads `source=jeunes_studios, lifecycle=unknown`, SIREN natif.
Puis **passe d'annotation navigateur** : ouvrir 8-10 fiches réelles issues des runs, annoter dans `architectes_groundtruth.csv` (`provenance=annuaire_*|delta_insee`), et relancer le gate `prescripteurs_run`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/ingestion/eval/architectes_groundtruth.csv backend/app/ingestion/eval/annuaires_snapshots backend/app/ingestion/eval/prescripteurs_metrics.py backend/app/ingestion/eval/prescripteurs_run.py backend/tests/test_annuaires_eval.py docs/a2-annuaires-design.md
git commit -m "feat(annuaires): eval elargie annuaire/delta + gate 0 faux merge + run reel borne + docs (Houzz reporte)"
```

(Le commit de `MEMORY.md` est hors dépôt `chr-signal-radar` : mettre à jour le fichier mémoire séparément, pas dans ce commit git.)

---

## Auto-relecture de cohérence inter-tâches

- **`population='architecte'` (A1) → réutilisée telle quelle** : les 3 connecteurs A2 émettent `population='architecte'` ; `_process_candidate` contourne déjà le classifieur CHR (branche `is_architecte` de A1) et saute l'enricher CHR. AUCUNE modification de cette branche n'est requise (le délta porte son SIREN natif ; l'annuaire est enrichi en amont dans `run_annuaires`). Le `main_signal` neutre `prescripteur actif` et le libellé T2 `portfolio hospitality/CHR` (A1, +2) sont réutilisés sans nouvelle famille de scoring → **scores CHR bit-à-bit identiques** (aucun nouveau libellé score-bearing). **Nuance population `architecte`** : lors d'une fusion douce annuaire×insta, `_merge_corroboration` comble des trous score-portants (`decision_maker`, `website`, `instagram`) → le score de CETTE fiche archi peut évoluer, c'est attendu. En revanche le tag `CORROBORATION_TAG` (« corroboré registre × instagram », +1) est désormais **exclu quand la source est `annuaire`** (garde ajoutée en T4) : pas de libellé score-bearing faux ni de +1 injustifié. Les scores CHR et A1 restent inchangés (jamais de source `annuaire`).
- **`match()` CHR jamais touché → `match_eval` 8/9 intact** : `match_architecte` est une fonction NEUVE, parallèle, gate `classify_naf_prescripteur` (séparé de `classify_naf`). Elle réutilise les helpers PURS (`search_by_name`, `_name_overlap`, `_geo_consistent`, `arbitrate`, `_result`, `_tokens`) sans les modifier. Le gate `match_eval` est re-vérifié en T4 et T5.
- **`run_prescripteurs` (A1) jamais modifié → gates A1 intacts** : l'enrichissement SIREN archi vit dans `run_annuaires` (T4), PAS dans `run_prescripteurs`. La fusion nom+ville de `_process_candidate` est gardée par `cand.source == "annuaire"` : un lead Insta entrant (run_prescripteurs) ne la déclenche jamais → `run_prescripteurs` bit-à-bit identique. `prescripteurs_run` gagne un gate `0 faux merge` ADDITIF qui tourne sur des **fixtures livrées** et des **paires de fusion RÉELLES** (`stats.soft_merges`) — jamais court-circuité à `True` ; les gates A1 existants inchangés.
- **Dédup — trois voies ordonnées, une seule fusion par candidat** : `_process_candidate` tente (1) upsert même-source (`source`+`source_ref`+`population`), (2) fusion SIREN cross-source (`corroborated`, existante — réconcilie annuaire↔délta quand le matcher a trouvé le SIREN), (3) fusion douce nom+ville (annuaire entrant seulement, réconcilie annuaire↔insta faute de SIREN commun). Chaque voie `return` après fusion → jamais de double comptage. La voie (3) est conservatrice : **exactement 1 fiche au même nom+ville ET une corroboration** (domaine de site, code postal, ou dirigeant) sinon rien — le nom+ville seul ne suffit pas (garde anti-homonyme fortuit). Chaque fusion (3) est tracée dans `stats.soft_merges` → gate **0 faux merge** mesuré sur des fusions réelles.
- **Connecteurs — HTTP toujours injectable, parsing PUR** : CFAI/UFDI/`polite_get` acceptent un `HtmlFetch` ; les tests alimentent des fixtures = extraits VERBATIM des HTML sondés (`.superpowers/sdd/sonde-a2/`). Le délta réutilise `insee.fetch_new_etablissements` (fetch injectable, déjà testé brique 2). **Aucun réseau en pytest.**
- **Robots respecté par construction** : CFAI n'expose que `?page=N` (permissif). UFDI n'utilise QUE `/decorateur/*.html` (Allow) — jamais `/membres.php` (Disallow). Documenté dans les docstrings des connecteurs et `a2-annuaires-design.md`.
- **Rendement fondé sur la MESURE** : le filtre `qualifies` (T3) encode exactement les mots-clés de la sonde (rendement 28 % visible / 9,8 % total) + le garde négatif justifié par les faux positifs 74.10Z observés (`DESIGN GRAPHIQUE`). Le délta est `lifecycle='unknown'` sans tier (flux bruyant, sonde), là où l'annuaire est `studio_actif` (source de confiance).
- **Houzz reporté, pas oublié** : aucun connecteur (sonde : anti-bot dur, ToS incertain), documenté dans `a2-annuaires-design.md` et « Hors périmètre A2 ».

## Hors périmètre A2 (à ne PAS implémenter ici)

- **Houzz automatisé** : anti-bot actif non-déterministe + ToS incertain (sonde). Documenté et REPORTÉ (éventuel A2bis manuel/API partenaire Houzz Pro). Aucun connecteur, aucun Playwright.
- **Filtre spécialité CFAI côté serveur** (POST + CSRF Hôtellerie/Restauration) : trop large (46 %) et bruité pour porter un tier ; on pagine le stock en GET simple.
- **Watchlist « nouveau projet »** (re-visite périodique des studios pour un booster T1 dynamique) → brique **A3**.
- **Balayage stale-annuaire** (revisite CFAI/UFDI + désactivation des fiches `source='annuaire'` dont la ref a disparu de l'annuaire) → **A3** : les fiches annuaire n'étant pas soumises à `_purge_requalified`, la désactivation d'un membre retiré est hors périmètre A2 (documenté dans la note `run_annuaires`).
- **Scheduling / cron** des runs annuaire/délta → hors A2 (runs manuels bornés).
- **Génération de messages** spécialisés prescripteurs → réutilise l'existant, non spécialisé ici.
- **Élargir le `match()` CHR** au NAF archi : on ajoute un chemin parallèle (`match_architecte`), on ne modifie PAS le matcher CHR (préserve `match_eval` 8/9).

---

## Notes de revue

Findings de revue appliqués au plan (2026-07-11) :

- **[important] Volume UFDI ~157, pas ~255** : corrigé partout (Goal, décision sonde #4, T2 interface, docstring connecteur, docs). Vérifié en rejouant `parse_list_page` sur `ufdi-france.html` (157 cartes `div.et_pb_team_member`). Les ~98 liens dept-nav (qui matchent `_PROFILE_RE`) sont exclus par le scope team_member ; garde du repli régional documentée ; piste « crawler les ~100 pages de listing départemental » notée si plus de volume est requis.
- **[important] Gate 0 faux merge câblé bout-en-bout** : `IngestStats.soft_merges` enregistre chaque paire `(ref_annuaire, ref_insta)` réellement fusionnée (émise par `_process_candidate`, propagée par `run_annuaires`) ; T5 consomme `stats.soft_merges` via `false_merges_annuaire_insta(pairs, truth)` sur fixtures LIVRÉES ; suppression du court-circuit `gate=True (n/a)`.
- **[important] Homonyme fortuit** : `_soft_dedup_architecte` exige désormais nom+ville identiques **ET** une corroboration (`_corroborates` : domaine site / code postal / dirigeant) ; nom+ville seul ne fusionne plus. Tests mis à jour (+ un test négatif sans corroboration).
- **[important] beautifulsoup4 absent** : Tech Stack corrigé (bs4 PAS dans `requirements.txt`, seulement `requests==2.32.3`) ; T1 rend l'ajout+install obligatoire et non conditionnel, avant tout import connecteur par `pipeline.py`.
- **[minor] Ville UFDI** : `fetch` préfère la commune de la carte (h6) au sous-titre profil (parfois un département) ; département conservé dans un champ séparé.
- **[minor] Tag corroboration** : `_merge_corroboration` exclut `source='annuaire'` du tag score-bearing « corroboré registre × instagram » (label faux + +1 injustifié pour la population archi) ; CHR/A1 inchangés.
- **[minor] Auto-purge annuaire** : documenté que les fiches `source='annuaire'` ne passent pas par `_purge_requalified` (pas de désactivation auto d'un membre retiré) ; balayage stale-annuaire renvoyé à A3.
- **[minor] Téléphone UFDI en fusion** : la voie soft-merge recopie `cand.raw['phone']` sur la fiche survivante (sinon perdu, `_merge_corroboration` n'y touche pas).
- **[minor] Signature `false_merges_annuaire_insta`** : interface alignée sur les 2 arguments `(pairs, truth_same_studio)` réellement utilisés.
