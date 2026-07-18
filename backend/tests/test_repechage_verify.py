"""CLI de VERIFICATION du magasin repechage (Brique C, phase 2) — aucun
reseau (`fetch` factice, patron `test_find_sites.py`), cache `find_site`
isole sur un fichier sqlite SEPARE (jamais `chr_signal_radar.db`)."""
from __future__ import annotations

import json
from datetime import date
from typing import Callable, Dict, List, Optional
from urllib.parse import quote, urlparse

from sqlmodel import Session, SQLModel, create_engine

from app.ingestion.repechage_scan import AmbiguRecord, AmbiguStore
from app.ingestion.repechage_verify import (
    VerifyResult,
    _AmbiguOpp,
    _ambigu_to_opp,
    _fetch_website_text,
    _make_cache_session,
    _meta_description,
    _visible_text,
    evaluate_site_content,
    run_repechage_verify,
    verify_ambigu,
)
from app.models import HandleVerdict


def _rec(siret="101", denom="GARRIGOS DESIGN", ville="Lyon", cp="69001",
         adresse="1 rue de la Republique", dirigeant=None, siren=None) -> AmbiguRecord:
    return AmbiguRecord(
        siret=siret, siren=siren or siret[:9], denomination=denom, ville=ville,
        cp=cp, adresse=adresse, dirigeant=dirigeant, naf="74.10Z",
        date_creation="2010-01-01", raison_rejet_v2="sans_marqueur_interieur",
        detection_date="2026-07-17",
    )


def _ddg_html(urls: List[str]) -> str:
    anchors = "".join(
        f'<a rel="nofollow" class="result__a" '
        f'href="//duckduckgo.com/l/?uddg={quote(u, safe="")}&amp;rut=x">titre</a>'
        for u in urls
    )
    return f"<html><body>{anchors}</body></html>"


def _dispatch_fetch(routes: Dict[str, str], pages: Dict[str, str]) -> Callable[[str], Optional[str]]:
    """``fetch`` factice : route les requetes DDG selon un fragment de la
    query normalisee (``+`` entre mots), sert les pages candidates par
    domaine (sans www). Zero reseau (patron ``test_find_sites._dispatch_fetch``)."""
    def fetch(url: str) -> Optional[str]:
        if "html.duckduckgo.com" in url:
            for frag, html in routes.items():
                if frag in url:
                    return html
            return None
        host = urlparse(url).netloc.lower()
        bare = host[4:] if host.startswith("www.") else host
        return pages.get(bare)
    return fetch


def _cache_engine():
    e = create_engine("sqlite://")
    SQLModel.metadata.create_all(e, tables=[HandleVerdict.__table__])
    return e


_INTERIOR_PAGE = """<html><head>
<title>Garrigos Design — Architecture d'intérieur à Lyon</title>
</head><body>
<h1>Garrigos Design</h1>
<p>Studio d'architecture d'intérieur, agencement sur-mesure pour particuliers.</p>
<footer>1 rue de la Republique, 69001 Lyon</footer>
</body></html>"""

_HARD_NEG_PAGE = """<html><head>
<title>Garrigos Design — Studio de design graphique</title>
</head><body>
<h1>Garrigos Design</h1>
<p>Identite visuelle, packaging et communication pour les marques.</p>
<footer>1 rue de la Republique, 69001 Lyon</footer>
</body></html>"""

_NO_MARKER_PAGE = """<html><head>
<title>Garrigos Design — Agencement sur mesure</title>
</head><body>
<h1>Garrigos Design</h1>
<p>Agencement et menuiserie sur mesure pour professionnels.</p>
<footer>1 rue de la Republique, 69001 Lyon</footer>
</body></html>"""


# --- evaluate_site_content (pur) --------------------------------------------------


def test_evaluate_site_content_positive_marker_confirms():
    ok, markers = evaluate_site_content(
        "Garrigos Design — Architecture d'intérieur à Lyon, agencement sur-mesure"
    )
    assert ok is True
    # Marqueurs = LOCUTIONS FORTES (accents/apostrophes replies), pas un token isole.
    assert "architecture d interieur" in markers


def test_evaluate_site_content_strong_locution_confirms_despite_adjacent_word():
    # REFONTE 2026-07-18 : « graphique » n'est PLUS une garde negative en
    # sous-chaine (elle rejetait a tort de vrais studios qui mentionnent une
    # activite adjacente). Une LOCUTION FORTE d'archi d'interieur confirme meme
    # a cote du mot « graphique » (cas reel studio-optimiste.fr : « pieces
    # graphiques » + « architecture d'interieur »).
    ok, markers = evaluate_site_content(
        "Studio de design graphique et d'architecture d'intérieur"
    )
    assert ok is True
    assert "architecture d interieur" in markers


def test_evaluate_site_content_isolated_token_home_does_not_confirm():
    # DEFAUT 1 (faux confirme NAA STUDIO) : un « Home » de menu, ou « interior »
    # isole dans un contexte produit, ne suffit PLUS -- il faut une LOCUTION.
    ok, markers = evaluate_site_content(
        "naa studio | unique handmade ceramic lamps  Home  art  modernity  interior"
    )
    assert ok is False
    assert markers == []


def test_evaluate_site_content_no_positive_marker_rejects():
    ok, markers = evaluate_site_content("Agencement et menuiserie sur mesure")
    assert ok is False
    assert markers == []


def test_evaluate_site_content_landscape_architect_rejected():
    ok, markers = evaluate_site_content(
        "Architecte paysagiste — amenagement de jardins et espaces exterieurs"
    )
    assert ok is False


def test_evaluate_site_content_pure_building_architect_rejected():
    ok, markers = evaluate_site_content(
        "Permis de construire et maison individuelle, maitrise d'oeuvre batiment"
    )
    assert ok is False


# --- helpers texte (purs) -----------------------------------------------------------


def test_visible_text_strips_scripts_and_tags():
    html = "<html><head><script>evil()</script></head><body><p>Bonjour</p></body></html>"
    text = _visible_text(html)
    assert "evil()" not in text
    assert "Bonjour" in text


def test_meta_description_extracts_content():
    html = '<html><head><meta name="description" content="Studio d\'intérieur"></head></html>'
    assert _meta_description(html) == "Studio d'intérieur"


def test_meta_description_absent_returns_empty():
    assert _meta_description("<html></html>") == ""


# --- _ambigu_to_opp (adaptateur, pur) ------------------------------------------------


def test_ambigu_to_opp_maps_fields():
    rec = _rec(siret="55", siren="555555555", dirigeant="Marie Studio")
    opp = _ambigu_to_opp(rec)
    assert isinstance(opp, _AmbiguOpp)
    assert opp.id is None
    assert opp.establishment_name == "GARRIGOS DESIGN"
    assert opp.city == "Lyon"
    assert opp.siren == "555555555"
    assert opp.siret == "55"
    assert opp.dirigeants == ["Marie Studio"]
    assert "69001" in opp.address


def test_ambigu_to_opp_empty_dirigeants_when_none():
    opp = _ambigu_to_opp(_rec(dirigeant=None))
    assert opp.dirigeants == []


# --- verify_ambigu (orchestration find_site + marqueurs, fetch factice) -------------


def test_verify_ambigu_confirms_when_site_found_with_interior_markers():
    fetch = _dispatch_fetch(
        {"garrigos+design+lyon": _ddg_html(["https://garrigosinterieur.fr/"])},
        {"garrigosinterieur.fr": _INTERIOR_PAGE},
    )
    with Session(_cache_engine()) as cache:
        result = verify_ambigu(_rec(), cache, fetch=fetch, today=date(2026, 7, 17))

    assert isinstance(result, VerifyResult)
    assert result.site_finder_verdict == "found"
    assert result.verdict == "confirme"
    assert result.website == "https://garrigosinterieur.fr/"
    assert "architecture d interieur" in result.marqueurs
    assert result.detail  # renseigne (name_signal ou repli)


def test_verify_ambigu_infirms_when_site_found_but_hard_negative():
    fetch = _dispatch_fetch(
        {"garrigos+design+lyon": _ddg_html(["https://garrigosinterieur.fr/"])},
        {"garrigosinterieur.fr": _HARD_NEG_PAGE},
    )
    with Session(_cache_engine()) as cache:
        result = verify_ambigu(_rec(), cache, fetch=fetch, today=date(2026, 7, 17))

    assert result.verdict == "infirme"
    assert result.marqueurs == []


def test_verify_ambigu_infirms_when_site_found_but_no_interior_marker():
    fetch = _dispatch_fetch(
        {"garrigos+design+lyon": _ddg_html(["https://garrigosinterieur.fr/"])},
        {"garrigosinterieur.fr": _NO_MARKER_PAGE},
    )
    with Session(_cache_engine()) as cache:
        result = verify_ambigu(_rec(), cache, fetch=fetch, today=date(2026, 7, 17))

    assert result.verdict == "infirme"
    assert result.detail == "site_sans_marqueur_interieur"


def test_verify_ambigu_sans_site_when_no_candidate_found():
    # DDG sert un resultat, mais c'est une plateforme (Instagram) -> own_site()
    # le filtre -> aucun candidat propre -> site_finder verdict "no_candidate".
    fetch = _dispatch_fetch(
        {"pango+studio+paris": _ddg_html(["https://www.instagram.com/pango/"])},
        {},
    )
    rec = _rec(siret="202", denom="PANGO STUDIO", ville="Paris", cp="75011")
    with Session(_cache_engine()) as cache:
        result = verify_ambigu(rec, cache, fetch=fetch, today=date(2026, 7, 17))

    assert result.site_finder_verdict == "no_candidate"
    assert result.verdict == "sans_site"
    assert result.detail == "no_candidate"
    assert result.website is None


def test_verify_ambigu_sans_site_when_website_unreachable_on_refetch(monkeypatch):
    # find_site attribue un site (found), mais le RE-fetch de ce module (via
    # _fetch_website_text) echoue -> jamais un jugement definitif sur une
    # simple panne (VIDE > FAUX) : sans_site, REESSAYABLE, jamais 'infirme'.
    import app.ingestion.repechage_verify as rv

    fetch = _dispatch_fetch(
        {"garrigos+design+lyon": _ddg_html(["https://garrigosinterieur.fr/"])},
        {"garrigosinterieur.fr": _INTERIOR_PAGE},
    )
    monkeypatch.setattr(rv, "_fetch_website_text", lambda fetch, website: None)

    with Session(_cache_engine()) as cache:
        result = rv.verify_ambigu(_rec(), cache, fetch=fetch, today=date(2026, 7, 17))

    assert result.site_finder_verdict == "found"  # find_site avait bien trouve un site
    assert result.verdict == "sans_site"
    assert result.detail == "site_injoignable_au_re_fetch"


def test_fetch_website_text_returns_none_when_domain_dead():
    fetch = _dispatch_fetch({}, {})
    assert _fetch_website_text(fetch, "https://mort.fr/") is None


def test_fetch_website_text_aggregates_home_content():
    fetch = _dispatch_fetch({}, {"garrigosinterieur.fr": _INTERIOR_PAGE})
    text = _fetch_website_text(fetch, "https://garrigosinterieur.fr/")
    assert text is not None
    assert "architecture d'intérieur" in text.lower() or "interieur" in text.lower()


# --- _make_cache_session (isolation totale de chr_signal_radar.db) -----------------


def test_make_cache_session_creates_only_handle_verdicts_table(tmp_path):
    import sqlite3

    cache_path = str(tmp_path / "repechage_cache.db")
    session = _make_cache_session(cache_path)
    session.close()

    conn = sqlite3.connect(cache_path)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    conn.close()
    assert "handle_verdicts" in tables
    assert "opportunities" not in tables


# --- run_repechage_verify (orchestration : persistance magasin, JSONL, reprise) ----


def test_run_repechage_verify_dry_run_persists_verdict_and_writes_jsonl(tmp_path):
    store = AmbiguStore(str(tmp_path / "ambigus.db"))
    store.save_candidate(_rec(siret="301"))

    fetch = _dispatch_fetch(
        {"garrigos+design+lyon": _ddg_html(["https://garrigosinterieur.fr/"])},
        {"garrigosinterieur.fr": _INTERIOR_PAGE},
    )
    out_path = tmp_path / "out.jsonl"
    with Session(_cache_engine()) as cache:
        stats = run_repechage_verify(
            limit=10, store=store, cache_session=cache, fetch=fetch, out=str(out_path),
        )

    assert stats.scanned == 1
    assert stats.confirme == 1
    assert store.verdict_counts() == {"confirme": 1}

    lines = out_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["verdict"] == "confirme"
    assert row["siret"] == "301"

    # Reprise : deja tranche 'confirme' -> jamais re-verifie.
    with Session(_cache_engine()) as cache2:
        stats2 = run_repechage_verify(limit=10, store=store, cache_session=cache2, fetch=fetch)
    assert stats2.scanned == 0
    store.close()


def test_run_repechage_verify_retries_sans_site(tmp_path):
    store = AmbiguStore(str(tmp_path / "ambigus.db"))
    store.save_candidate(_rec(siret="401", denom="PANGO STUDIO", ville="Paris", cp="75011"))

    fetch_no_candidate = _dispatch_fetch(
        {"pango+studio+paris": _ddg_html(["https://www.instagram.com/pango/"])}, {},
    )
    with Session(_cache_engine()) as cache:
        stats1 = run_repechage_verify(limit=10, store=store, cache_session=cache,
                                      fetch=fetch_no_candidate)
    assert stats1.sans_site == 1
    assert store.verdict_counts() == {"sans_site": 1}

    # Un site apparait au run suivant -> reessaye (sans_site n'est PAS definitif).
    fetch_found = _dispatch_fetch(
        {"pango+studio+paris": _ddg_html(["https://pangointerieur.fr/"])},
        {"pangointerieur.fr": _INTERIOR_PAGE.replace("Lyon", "Paris").replace("69001", "75011")},
    )
    with Session(_cache_engine()) as cache2:
        stats2 = run_repechage_verify(limit=10, store=store, cache_session=cache2,
                                      fetch=fetch_found)
    assert stats2.scanned == 1
    store.close()


# --- GT ADVERSE REEL 2026-07-18 (snapshots HTML reels, recuperes poliment) --------
#     Fixtures dans tests/fixtures/repechage_verify/ (head + metas + region du
#     texte, trimme). Chaque cas du gate devient un test de non-regression.

import os

import pytest

from app.ingestion.repechage_verify import _aggregate_site_text

_FIXDIR = os.path.join(os.path.dirname(__file__), "fixtures", "repechage_verify")


def _fixture_html(name: str) -> str:
    with open(os.path.join(_FIXDIR, name + ".html"), encoding="utf-8") as f:
        return f.read()


# 7 des 8 VRAIS studios infirmes A TORT au gate -> doivent CONFIRMER (rendement).
# mtstudio-archi.com EXCLU a dessein : SPA Canva 100 % JS dont la seule mention
# « architecture d'interieur » vit dans un blob <script> JSON en mojibake
# (« intÃ©rieur ») -- illisible par un scan HTML statique poli (VIDE > FAUX,
# limite honnete assumee, cf. rapport). Ce faux negatif residuel est documente
# par test_gt_mtstudio_js_spa_stays_empty ci-dessous.
@pytest.mark.parametrize("fixture", [
    "hoquet",       # « Architecture d'interieur designer global Rhone »
    "nais",         # studio d'architecture d'interieur, Toulouse
    "valentine",    # architecte d'interieur et decoratrice
    "optimiste",    # decoration + architecture d'interieur + suivi de chantier
    "edel",         # designer d'interieur ecoresponsable, home staging
    "nkdeco",       # relooking deco d'interieur, home staging
    "accord",       # site en maintenance, « architecture interieure » en og:image:alt
    "aethos",       # anglophone : « interior architect » (avait confirme sur « home » nu)
    "ellie",        # anglophone : « interior designer » (avait confirme sur « home » nu)
])
def test_gt_real_studio_now_confirms(fixture):
    ok, markers = evaluate_site_content(_aggregate_site_text(_fixture_html(fixture)))
    assert ok is True, f"{fixture} devrait CONFIRMER (vrai studio)"
    assert markers, f"{fixture} : au moins une locution forte attendue"


# CONTRE-GARDE : les infirmes LEGITIMES du GT restent infirmes (precision).
# naa = defaut 1 (marque serbe de lampes), + fabrication / 3D / design graphique.
@pytest.mark.parametrize("fixture", [
    "naa",          # DEFAUT 1 : naa-studio.com, lampes ceramique serbes (« Home » de menu)
    "dpdesign",     # dpdesignandfabrication.com : fabrication
    "mooka",        # mooka3d.studio : 3D
    "frappe",       # frappe-design.com : design graphique
])
def test_gt_legit_infirme_stays_infirme(fixture):
    ok, markers = evaluate_site_content(_aggregate_site_text(_fixture_html(fixture)))
    assert ok is False, f"{fixture} doit RESTER infirme (contre-garde)"
    assert markers == []


def test_english_role_locution_confirms_but_bare_interior_design_does_not():
    # Discrimination anglaise : « interior designer/architect » (ROLE) confirme,
    # mais « interior design » NU ne confirme PAS -- un fabricant/entrepreneur
    # travaille POUR des projets de « interior design » sans etre lui-meme un
    # studio (contre-garde dpdesignandfabrication.com : « interior design ...
    # design fabrication construction contractor san francisco »).
    ok_role, _ = evaluate_site_content("We are an interior designer studio in Nantes.")
    assert ok_role is True
    ok_bare, markers = evaluate_site_content(
        "dpdesign interior design photo photography design fabrication contractor san francisco"
    )
    assert ok_bare is False
    assert markers == []


def test_gt_mtstudio_js_spa_stays_empty():
    # Limite honnete : contenu JS (SPA Canva) + mojibake dans un <script> ->
    # aucun texte lisible par un scan statique. VIDE (sans_site/infirme),
    # jamais un faux confirme.
    ok, markers = evaluate_site_content(_aggregate_site_text(_fixture_html("mtstudio")))
    assert ok is False
    assert markers == []


def test_meta_og_image_alt_is_scanned():
    # accord-conception.fr : la seule mention du metier est dans un
    # <meta property="og:image:alt"> -> _aggregate_site_text scanne TOUS les
    # attributs content de <meta>, pas seulement name="description".
    from app.ingestion.repechage_verify import _meta_contents
    html = ('<html><head><meta property="og:image:alt" '
            "content=\"inspirations d'architecture intérieure\"></head></html>")
    assert "architecture" in _meta_contents(html).lower()
    ok, _ = evaluate_site_content(_aggregate_site_text(html))
    assert ok is True


def test_internal_pages_scanned_for_markers():
    # Le marqueur peut ne figurer que sur une page interne (a-propos/prestations).
    from app.ingestion.repechage_verify import _fetch_website_text
    home = ('<html><head><title>Studio Bloom</title></head><body>'
            '<a href="/a-propos">A propos</a></body></html>')
    about = ('<html><head><title>A propos</title></head><body>'
             "<p>Architecte d'intérieur à Nantes, agencement sur-mesure.</p>"
             '</body></html>')
    fetch = _dispatch_fetch({}, {"studiobloom.fr": home})
    # page interne servie a part (le _dispatch_fetch ne route que par domaine :
    # on enrichit la home avec un lien, la page interne renvoie 'about').
    def fetch2(url):
        if url.rstrip("/").endswith("a-propos"):
            return about
        return fetch(url)
    text = _fetch_website_text(fetch2, "https://studiobloom.fr/")
    ok, markers = evaluate_site_content(text)
    assert ok is True
    assert "architecte d interieur" in markers


def test_run_repechage_verify_apply_raises_and_never_touches_store(tmp_path):
    store = AmbiguStore(str(tmp_path / "ambigus.db"))
    store.save_candidate(_rec(siret="501"))

    raised = False
    try:
        run_repechage_verify(limit=10, store=store, apply=True)
    except NotImplementedError:
        raised = True
    assert raised
    assert store.verdict_counts() == {}  # aucune ecriture, refus immediat
    assert len(store.list_unverified()) == 1  # rien consomme
    store.close()
