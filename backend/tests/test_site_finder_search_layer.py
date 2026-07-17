"""Tests de la COUCHE DE RECHERCHE fiabilisée du moteur de découverte de site
(Brique A, ``site_finder``).

Constat corrigé : DuckDuckGo HTML répond par des défis anti-bot (HTTP 202) même
à cadence lente -> les vrais sites étaient perdus AVANT le verrou d'identité.
Correctifs vérifiés ici, TOUS sans réseau (fixtures / ``requests`` factice,
``time.sleep`` neutralisé) :

1. Cadence RECHERCHE dédiée (>= 10 s + jitter), séparée du throttle 2,5 s des
   pages, avec UN retry après backoff long sur défi, sinon abandon fail-soft.
2. Moteur de REPLI : liste ordonnée (DDG puis Bing HTML), chacun avec parseur +
   détection d'échec, testable unitairement.
3. Distinction « recherche NON SERVIE » (moteurs muets -> ``search_unavailable``,
   RÉESSAYABLE) vs « vraiment aucun candidat » (moteur servi -> ``no_candidate``).
4. Le cache ne stocke JAMAIS un échec de moteur comme un résultat vide (le repli
   Bing compris)."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Dict, List, Optional
from urllib.parse import quote, urlparse

from sqlmodel import Session, SQLModel, create_engine

from app.ingestion import verdict_cache
from app.ingestion.enrichment import site_finder
from app.ingestion.enrichment.site_finder import (
    _ENGINES,
    _MIN_INTERVAL,
    _SEARCH_MIN_INTERVAL,
    SearchEngine,
    SearchOutcome,
    _is_search_url,
    _looks_like_challenge,
    _polite_search_get,
    _run_engines,
    _search,
    _search_attempt,
    _search_cache_key,
    find_site,
    parse_bing_results,
)

FIXTURES = Path(__file__).parent / "fixtures" / "site_finder"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _engine():
    e = create_engine("sqlite://")
    SQLModel.metadata.create_all(e)
    return e


def _ddg_html(urls: List[str]) -> str:
    anchors = "".join(
        f'<a rel="nofollow" class="result__a" '
        f'href="//duckduckgo.com/l/?uddg={quote(u, safe="")}&amp;rut=x">titre</a>'
        for u in urls
    )
    return f"<html><body>{anchors}</body></html>"


def _bing_html(urls: List[str]) -> str:
    blocks = "".join(
        f'<li class="b_algo"><h2><a href="{u}">titre</a></h2></li>' for u in urls
    )
    return f"<html><body><ol id=\"b_results\">{blocks}</ol></body></html>"


def _opp(**kw):
    base = dict(
        id=1, establishment_name="Atelier Dupont", city="Lyon",
        address="12 rue de la Republique, 69001 Lyon",
        dirigeants=["Chiara Rossi, Gérante"], siren="123456789",
        siret="12345678900012",
    )
    base.update(kw)
    return SimpleNamespace(**base)


# --------------------------------------------------------------------------- #
# 1. parse_bing_results (pur, sans réseau).                                    #
# --------------------------------------------------------------------------- #

def test_parse_bing_results_decodes_ck_redirect():
    # Fixture réaliste : le 1er résultat est une redirection /ck/a?u=a1<base64>.
    urls = parse_bing_results(_read("bing_results.html"))
    assert urls[0] == "https://atelier-dupont.fr/"
    assert "https://www.instagram.com/atelierdupont/" in urls


def test_parse_bing_results_keeps_direct_href():
    urls = parse_bing_results(_bing_html(["https://direct-site.fr/"]))
    assert urls == ["https://direct-site.fr/"]


def test_parse_bing_results_dedups_by_domain_and_caps():
    many = [f"https://s{i}.fr/" for i in range(12)]
    assert len(parse_bing_results(_bing_html(many))) == 8
    dup = _bing_html(["https://exemple.fr/a", "https://exemple.fr/b"])
    assert parse_bing_results(dup) == ["https://exemple.fr/a"]


def test_parse_bing_results_empty_and_malformed():
    assert parse_bing_results(None) == []
    assert parse_bing_results("") == []
    assert parse_bing_results("<html>rien d'organique</html>") == []


# --------------------------------------------------------------------------- #
# 2. Détection de défi anti-bot (pure).                                        #
# --------------------------------------------------------------------------- #

def test_looks_like_challenge_detects_anomaly_page():
    assert _looks_like_challenge(_read("ddg_challenge.html")) is True
    assert _looks_like_challenge("<html>Please solve the CAPTCHA</html>") is True


def test_looks_like_challenge_false_on_normal_or_empty():
    assert _looks_like_challenge(None) is False
    assert _looks_like_challenge("") is False
    assert _looks_like_challenge(_read("site_match.html")) is False
    assert _looks_like_challenge(_ddg_html(["https://x.fr/"])) is False


# --------------------------------------------------------------------------- #
# 3. Liste ORDONNÉE de moteurs + repli (via fetch injecté, sans réseau).       #
# --------------------------------------------------------------------------- #

def test_engines_are_ordered_ddg_then_bing():
    assert [e.name for e in _ENGINES] == ["duckduckgo", "bing"]
    assert all(isinstance(e, SearchEngine) for e in _ENGINES)


def test_run_engines_uses_ddg_first_and_skips_bing_when_served():
    calls: List[str] = []

    def fetch(url: str) -> Optional[str]:
        calls.append(url)
        if "duckduckgo" in url:
            return _ddg_html(["https://atelier-dupont.fr/"])
        raise AssertionError("Bing ne doit PAS être interrogé si DDG a servi")

    outcome = _run_engines("atelier dupont lyon", fetch)
    assert outcome.served is True
    assert outcome.engine == "duckduckgo"
    assert outcome.urls == ["https://atelier-dupont.fr/"]
    assert all("bing" not in c for c in calls)


def test_run_engines_falls_back_to_bing_when_ddg_mute():
    def fetch(url: str) -> Optional[str]:
        if "duckduckgo" in url:
            return None  # DDG muet (202/défi)
        if "bing" in url:
            return _bing_html(["https://atelier-dupont.fr/"])
        return None

    outcome = _run_engines("atelier dupont lyon", fetch)
    assert outcome.served is True
    assert outcome.engine == "bing"
    assert outcome.urls == ["https://atelier-dupont.fr/"]


def test_run_engines_falls_back_when_ddg_serves_empty_body():
    # DDG répond mais son parseur ne trouve rien (corps vide/malformé) -> Bing.
    def fetch(url: str) -> Optional[str]:
        if "duckduckgo" in url:
            return "<html><body>rien</body></html>"
        if "bing" in url:
            return _bing_html(["https://atelier-dupont.fr/"])
        return None

    outcome = _run_engines("q", fetch)
    assert outcome.served is True and outcome.engine == "bing"


def test_run_engines_all_mute_is_not_served():
    outcome = _run_engines("q", lambda url: None)
    assert outcome.served is False
    assert outcome.urls == []
    assert outcome.engine is None


# --------------------------------------------------------------------------- #
# 4. Cache de recherche : jamais un échec de moteur stocké comme vide.         #
# --------------------------------------------------------------------------- #

def test_search_caches_served_results_only():
    with Session(_engine()) as s:
        def fetch(url: str) -> Optional[str]:
            if "duckduckgo" in url:
                return _ddg_html(["https://atelier-dupont.fr/"])
            return None

        outcome = _search("atelier dupont lyon", s, fetch, today=date(2026, 7, 14))
        assert outcome.served is True
        # La recherche servie EST en cache.
        assert verdict_cache.get(s, _search_cache_key("atelier dupont lyon")) is not None


def test_search_mute_is_never_cached_as_empty():
    with Session(_engine()) as s:
        # Tous moteurs muets -> rien en cache (sinon panne anti-bot empoisonnée).
        outcome = _search("obscure studio", s, lambda url: None, today=date(2026, 7, 14))
        assert outcome.served is False
        assert verdict_cache.get(s, _search_cache_key("obscure studio")) is None


def test_search_cache_hit_is_served():
    with Session(_engine()) as s:
        served = _ddg_html(["https://atelier-dupont.fr/"])
        calls: List[str] = []

        def fetch(url: str) -> Optional[str]:
            calls.append(url)
            return served if "duckduckgo" in url else None

        _search("q lyon", s, fetch, today=date(2026, 7, 14))
        n = len(calls)
        again = _search("q lyon", s, fetch, today=date(2026, 7, 14))
        assert again.served is True and again.engine == "cache"
        assert len(calls) == n  # 2e recherche = 0 réseau (cache)


# --------------------------------------------------------------------------- #
# 5. Cadence dédiée + retry/backoff de _polite_search_get (requests factice).  #
# --------------------------------------------------------------------------- #

def test_search_cadence_is_slower_and_separate_from_pages():
    assert _SEARCH_MIN_INTERVAL >= 10.0
    assert _SEARCH_MIN_INTERVAL > _MIN_INTERVAL


def test_is_search_url_routes_engines_only():
    assert _is_search_url("https://html.duckduckgo.com/html/?q=x") is True
    assert _is_search_url("https://www.bing.com/search?q=x") is True
    assert _is_search_url("https://atelier-dupont.fr/") is False


class _FakeResp:
    def __init__(self, status: int, text: str, ctype: str = "text/html; charset=utf-8"):
        self.status_code = status
        self.text = text
        self.headers = {"content-type": ctype}


def _patch_net(monkeypatch, responses: List[_FakeResp]):
    """Installe un ``requests.get`` factice (séquence de réponses) et neutralise
    ``time.sleep`` (aucune attente réelle : cadence + backoff enregistrés)."""
    seq = iter(responses)
    got: List[str] = []
    slept: List[float] = []

    def fake_get(url, headers=None, timeout=None):
        got.append(url)
        return next(seq)

    monkeypatch.setattr(site_finder.requests, "get", fake_get)
    monkeypatch.setattr(site_finder.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(site_finder.random, "uniform", lambda a, b: a)
    site_finder._last_search_call[0] = 0.0
    return got, slept


def test_polite_search_get_retries_once_after_202_then_succeeds(monkeypatch):
    got, slept = _patch_net(monkeypatch, [
        _FakeResp(202, "<html>anomaly detected</html>"),  # défi -> retry
        _FakeResp(200, _ddg_html(["https://x.fr/"])),      # OK au retry
    ])
    html = _polite_search_get("https://html.duckduckgo.com/html/?q=x")
    assert html is not None and "result__a" in html
    assert len(got) == 2                      # exactement UN retry
    assert any(s >= 30.0 for s in slept)      # backoff LONG entre les deux


def test_polite_search_get_gives_up_after_second_challenge(monkeypatch):
    got, _ = _patch_net(monkeypatch, [
        _FakeResp(202, "<html>anomaly</html>"),
        _FakeResp(202, "<html>anomaly</html>"),  # persiste -> abandon
    ])
    assert _polite_search_get("https://html.duckduckgo.com/html/?q=x") is None
    assert len(got) == 2  # une tentative + UN retry, pas plus (jamais de spam)


def test_polite_search_get_detects_200_challenge_body(monkeypatch):
    got, _ = _patch_net(monkeypatch, [
        _FakeResp(200, _read("ddg_challenge.html")),  # 200 mais corps de défi
        _FakeResp(200, _read("ddg_challenge.html")),
    ])
    assert _polite_search_get("https://www.bing.com/search?q=x") is None
    assert len(got) == 2


def test_search_attempt_rejects_non_html_and_challenge(monkeypatch):
    monkeypatch.setattr(site_finder.requests, "get",
                        lambda url, headers=None, timeout=None: _FakeResp(
                            200, "{}", ctype="application/json"))
    assert _search_attempt("https://html.duckduckgo.com/html/?q=x") is None


# --------------------------------------------------------------------------- #
# 6. Intégration find_site : repli Bing sauve un vrai site perdu par DDG 202.  #
# --------------------------------------------------------------------------- #

def test_find_site_ddg_challenge_falls_back_to_bing_and_finds(monkeypatch):
    with Session(_engine()) as s:
        opp = _opp()
        challenge = _read("ddg_challenge.html")
        # Domaine NON devinable : la découverte doit vraiment passer par le repli
        # Bing (la devinette de domaine échoue sur ce domaine).
        bing = _bing_html(["https://les-ateliers-dupont-lyon.fr/"])
        match = _read("site_match.html")

        def fetch(url: str) -> Optional[str]:
            if "html.duckduckgo.com" in url:
                return None  # DDG sert un défi -> _polite_get réel rendrait None
            if "bing.com" in url:
                return bing
            host = urlparse(url).netloc.lower()
            bare = host[4:] if host.startswith("www.") else host
            return match if bare == "les-ateliers-dupont-lyon.fr" else None

        result = find_site(opp, s, fetch=fetch, today=date(2026, 7, 14))
        assert result.verdict == "found"
        assert result.website == "https://les-ateliers-dupont-lyon.fr/"
        # Le vrai site aurait été PERDU (no_candidate) sans le repli Bing.


# --------------------------------------------------------------------------- #
# 7. Intégration : distinction search_unavailable vs no_candidate.             #
# --------------------------------------------------------------------------- #

def test_find_site_all_engines_mute_is_search_unavailable_and_not_cached():
    with Session(_engine()) as s:
        opp = _opp()
        r1 = find_site(opp, s, fetch=lambda url: None, today=date(2026, 7, 14))
        assert r1.verdict == "search_unavailable"
        assert r1.from_cache is False
        # Non mis en cache -> une reprise (même jour) re-cherche et trouve.
        good_ddg = _ddg_html(["https://atelier-dupont.fr/"])
        match = _read("site_match.html")

        def good(url: str) -> Optional[str]:
            if "html.duckduckgo.com" in url:
                return good_ddg
            host = urlparse(url).netloc.lower()
            bare = host[4:] if host.startswith("www.") else host
            return match if bare == "atelier-dupont.fr" else None

        r2 = find_site(opp, s, fetch=good, today=date(2026, 7, 14))
        assert r2.from_cache is False
        assert r2.verdict == "found"


def test_find_site_engine_served_but_only_platforms_is_no_candidate():
    with Session(_engine()) as s:
        opp = _opp()
        # DDG SERT des résultats (donc pas "muet"), mais uniquement des
        # plateformes filtrées par own_site -> vrai "no_candidate", cacheable.
        ddg = _ddg_html(["https://www.instagram.com/atelierdupont/",
                         "https://www.facebook.com/atelierdupont/"])

        def fetch(url: str) -> Optional[str]:
            if "html.duckduckgo.com" in url:
                return ddg
            return None  # Bing muet, mais DDG a servi -> pas search_unavailable

        result = find_site(opp, s, fetch=fetch, today=date(2026, 7, 14))
        assert result.verdict == "no_candidate"
        assert result.candidates == []
