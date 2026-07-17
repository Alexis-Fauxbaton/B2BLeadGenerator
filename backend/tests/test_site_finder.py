"""Tests du moteur de découverte de site (Brique A, ``site_finder``).

Couvre SANS RÉSEAU (fixtures HTML sous ``fixtures/site_finder/``, ``fetch``
factice) : normalisation du nom, tokens significatifs, parsing DDG (décodage
``uddg``, dédup), verrou A (contenu / domaine), verrou B (chaque signal
isolément), extraction des marqueurs d'identité, et le pipeline complet
``find_site`` — nominal, homonymes REFUSÉS (doctrine VIDE > FAUX), plateformes
exclues, cache de requête partagé entre fiches."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Dict, List, Optional
from urllib.parse import quote, urlparse

from sqlmodel import Session, SQLModel, create_engine

from app.ingestion.enrichment import site_finder
from app.ingestion.enrichment.site_finder import (
    _ENGINE_VERSION,
    SiteFindResult,
    _build_queries,
    _check_lock_a,
    _check_lock_b,
    _check_lock_c,
    _dirigeant_family_name,
    _dirigeant_full_name,
    _dirigeant_identity_tokens,
    _domain,
    _domain_matches_name,
    _extract_postal_code,
    _guess_domains,
    _verdict_handle,
    extract_identity_markers,
    find_site,
    normalize_name,
    parse_ddg_results,
    significant_tokens,
)
from app.models import Opportunity

FIXTURES = Path(__file__).parent / "fixtures" / "site_finder"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _engine():
    e = create_engine("sqlite://")
    SQLModel.metadata.create_all(e)
    return e


def _mk_opp(session: Session, **overrides) -> Opportunity:
    base = dict(
        establishment_name="Atelier Dupont", establishment_type="architecte d'intérieur",
        city="Lyon", address="12 rue de la Republique, 69001 Lyon",
        main_signal="prescripteur actif", detection_date=date(2026, 7, 10),
        estimated_timing="J-90", source="sirene_stock", population="architecte",
        dirigeants=["Chiara Rossi, Gérante"], siren="123456789", siret="12345678900012",
    )
    base.update(overrides)
    opp = Opportunity(**base)
    session.add(opp)
    session.commit()
    session.refresh(opp)
    return opp


def _ddg_html(urls: List[str]) -> str:
    """Construit une page de résultats DDG minimale (ancres ``result__a``
    encapsulées en redirection ``uddg=``) pour une liste d'URLs cibles."""
    anchors = "".join(
        f'<a rel="nofollow" class="result__a" '
        f'href="//duckduckgo.com/l/?uddg={quote(u, safe="")}&amp;rut=x">titre</a>'
        for u in urls
    )
    return f"<html><body>{anchors}</body></html>"


def _fake_fetch(
    ddg_html: str, pages_by_domain: Dict[str, str], ddg_calls: Optional[List[str]] = None,
) -> Callable[[str], Optional[str]]:
    """``fetch`` factice : sert ``ddg_html`` pour toute requête DuckDuckGo,
    et le HTML enregistré pour le DOMAINE (sans www) de toute autre URL —
    zéro réseau."""
    def fetch(url: str) -> Optional[str]:
        if "html.duckduckgo.com" in url:
            if ddg_calls is not None:
                ddg_calls.append(url)
            return ddg_html
        host = urlparse(url).netloc.lower()
        bare = host[4:] if host.startswith("www.") else host
        return pages_by_domain.get(bare)
    return fetch


# --- normalize_name / significant_tokens (purs) ---------------------------------


def test_normalize_name_strips_legal_forms_accents_punct():
    assert normalize_name("Atelier Dupont SARL, Décoration & Cie.") == \
        "atelier dupont decoration cie"


def test_normalize_name_empty_inputs():
    assert normalize_name(None) == ""
    assert normalize_name("") == ""


def test_normalize_name_collapses_multiple_spaces():
    assert normalize_name("  Studio   Dupont  ") == "studio dupont"


def test_significant_tokens_filters_generic_stoplist():
    assert significant_tokens("Atelier Dupont") == ["dupont"]


def test_significant_tokens_drops_short_tokens():
    assert significant_tokens("Le Studio de Ax") == ["studio"]  # "ax"/"le"/"de" < 3 caractères


def test_significant_tokens_falls_back_when_all_generic():
    # Nom entièrement générique -> repli sur les tokens bruts (JAMAIS vide,
    # sinon le verrou A serait trivialement vrai).
    assert significant_tokens("Studio Design Paris") == ["studio", "design", "paris"]


# --- parse_ddg_results (pur) -----------------------------------------------------


def test_parse_ddg_results_decodes_uddg_and_dedups_by_domain():
    html = _read("ddg_results.html")
    urls = parse_ddg_results(html)
    assert urls == [
        "https://atelier-dupont.fr/",
        "https://www.instagram.com/atelierdupont/",
        "https://atelier-dupont-paris.fr/",
    ]


def test_parse_ddg_results_empty_html():
    assert parse_ddg_results(None) == []
    assert parse_ddg_results("") == []
    assert parse_ddg_results("<html><body>rien</body></html>") == []


def test_parse_ddg_results_dedups_same_domain_keeps_first():
    html = _ddg_html(["https://exemple.fr/a", "https://exemple.fr/b"])
    assert parse_ddg_results(html) == ["https://exemple.fr/a"]


def test_parse_ddg_results_caps_to_eight():
    urls = [f"https://site{i}.fr/" for i in range(12)]
    html = _ddg_html(urls)
    assert len(parse_ddg_results(html)) == 8


def test_parse_ddg_results_keeps_direct_url_as_is():
    html = '<a class="result__a" href="https://direct-site.fr/">direct</a>'
    assert parse_ddg_results(html) == ["https://direct-site.fr/"]


# --- extract_identity_markers (pur) ----------------------------------------------


def test_extract_identity_markers_aggregates_title_h1_og():
    html = ('<title>Mon Titre</title><h1>Un Sous-Titre</h1>'
           '<meta property="og:site_name" content="OG Name">')
    text = extract_identity_markers(html)
    assert "Mon Titre" in text
    assert "Un Sous-Titre" in text
    assert "OG Name" in text


def test_extract_identity_markers_empty_html():
    assert extract_identity_markers("") == ""


# --- Verrou A (pur) ---------------------------------------------------------------


def test_lock_a_content_all_tokens_present_wins():
    signal = _check_lock_a(["dupont"], "Atelier Dupont - Architecte d'intérieur",
                           "sans-rapport.fr")
    assert signal == "A1_content"


def test_lock_a_content_missing_token_falls_to_domain():
    # "dupont" absent du contenu -> A1 échoue ; le domaine seul décide.
    signal = _check_lock_a(["dupont"], "Studio Meridien - Architecte", "atelier-dupont.fr")
    assert signal == "A2_domain"


def test_lock_a_fails_when_neither_content_nor_domain_match():
    signal = _check_lock_a(["dupont"], "Studio Meridien", "studio-meridien.fr")
    assert signal is None


def test_domain_matches_name_fuzzy_ratio():
    assert _domain_matches_name("atelierdupont.fr", ["atelier", "dupont"]) is True
    assert _domain_matches_name("totalement-different.fr", ["dupont"]) is False


def test_domain_matches_name_contiguous_substring():
    # atelierdupont.fr ~ « Atelier Dupont » (cf. spec, sous-chaîne contiguë)
    assert _domain_matches_name("atelier-dupont.fr", ["dupont"]) is True


def test_domain_matches_name_empty_inputs():
    assert _domain_matches_name(None, ["dupont"]) is False
    assert _domain_matches_name("atelier-dupont.fr", []) is False


# --- Verrou B (pur), chaque signal isolément --------------------------------------


def _fiche():
    return SimpleNamespace(
        city="Lyon", address="12 rue X, 69001 Lyon",
        dirigeants=["Chiara Rossi, Gérante"], siren="123456789", siret="12345678900012",
    )


def test_lock_b_ville_signal_alone():
    assert _check_lock_b(_fiche(), "Nous sommes situes a Lyon, au centre-ville.") == ["ville"]


def test_lock_b_cp_signal_alone():
    assert _check_lock_b(_fiche(), "Code postal : 69001, nulle part ailleurs.") == ["cp"]


def test_lock_b_dirigeant_signal_alone():
    assert _check_lock_b(_fiche(), "Gerante : Chiara Rossi, contact direct.") == ["dirigeant"]


def test_lock_b_siren_signal_alone():
    assert _check_lock_b(_fiche(), "SIREN : 123 456 789, RCS Paris.") == ["siren"]


def test_lock_b_siret_signal_alone():
    assert _check_lock_b(_fiche(), "SIRET : 123 456 789 00012.") == ["siret"]


def test_lock_b_no_signal_when_nothing_matches():
    assert _check_lock_b(_fiche(), "Aucune information utile sur cette page.") == []


def test_lock_b_short_family_name_rejected():
    # Nom de famille < 3 caracteres apres normalisation -> jamais retenu.
    fiche = SimpleNamespace(city="", address="", dirigeants=["Jean Vu, Gerant"],
                            siren=None, siret=None)
    assert _check_lock_b(fiche, "Gerant : Jean Vu, disponible.") == []


def test_extract_postal_code_boundary():
    assert _extract_postal_code("12 rue X, 69001 Lyon") == "69001"
    assert _extract_postal_code(None) is None
    assert _extract_postal_code("pas de code ici") is None


def test_dirigeant_full_and_family_name():
    assert _dirigeant_full_name(["Samuel Afif, Président"]) == "Samuel Afif"
    assert _dirigeant_full_name(None) is None
    assert _dirigeant_full_name([]) is None
    assert _dirigeant_family_name(["Samuel Afif, Président"]) == "afif"
    assert _dirigeant_family_name(["Jean Vu, Gérant"]) is None  # "vu" < 3 caractères


# --- Pipeline complet find_site (fetch factice, zéro réseau) ---------------------


def test_find_site_nominal_found_with_multi_signal_corroboration():
    with Session(_engine()) as s:
        opp = _mk_opp(s)
        fetch = _fake_fetch(_read("ddg_results.html"),
                            {"atelier-dupont.fr": _read("site_match.html")})
        result = find_site(opp, s, fetch=fetch, today=date(2026, 7, 14))

        assert result.verdict == "found"
        assert result.website == "https://atelier-dupont.fr/"
        assert result.name_signal in ("A1_content", "A2_domain")
        assert result.corroboration  # au moins un signal
        assert not result.from_cache
        assert isinstance(result, SiteFindResult)


def test_find_site_homonym_same_name_other_city_is_refused():
    with Session(_engine()) as s:
        opp = _mk_opp(s)
        ddg_html = _ddg_html(["https://atelier-dupont-homonyme.fr/"])
        fetch = _fake_fetch(
            ddg_html, {"atelier-dupont-homonyme.fr": _read("site_homonym_othercity.html")}
        )
        result = find_site(opp, s, fetch=fetch, today=date(2026, 7, 14))

        # A passe (même nom) mais B échoue completement -> VIDE > FAUX.
        assert result.verdict == "locked_out"
        assert result.website is None
        assert result.candidates == ["atelier-dupont-homonyme.fr"]
        assert len(result.inspected) == 1
        assert result.inspected[0]["a_pass"] is True
        assert result.inspected[0]["b_signals"] == []


def test_find_site_homonym_same_cp_different_name_is_refused():
    with Session(_engine()) as s:
        opp = _mk_opp(s)
        ddg_html = _ddg_html(["https://studio-meridien.fr/"])
        fetch = _fake_fetch(
            ddg_html, {"studio-meridien.fr": _read("site_homonym_samecp.html")}
        )
        result = find_site(opp, s, fetch=fetch, today=date(2026, 7, 14))

        # B-cp aurait pu passer mais A echoue (nom different) -> REFUS.
        assert result.verdict == "locked_out"
        assert result.website is None
        assert len(result.inspected) == 1
        assert result.inspected[0]["a_pass"] is False
        assert "cp" in result.inspected[0]["b_signals"]


def test_find_site_platforms_only_yields_no_candidate_without_page_fetch():
    with Session(_engine()) as s:
        opp = _mk_opp(s)
        ddg_html = _ddg_html([
            "https://www.instagram.com/atelierdupont/",
            "https://www.houzz.fr/pro/atelierdupont/__public",
        ])
        fetched: List[str] = []

        def fetch(url: str) -> Optional[str]:
            fetched.append(url)
            if "html.duckduckgo.com" in url:
                return ddg_html
            return None  # domaines devinés morts ; aucune page candidate propre

        result = find_site(opp, s, fetch=fetch, today=date(2026, 7, 14))

        assert result.verdict == "no_candidate"
        assert result.website is None
        assert result.candidates == []
        assert result.inspected == []
        # Les plateformes (instagram/houzz) sont filtrées par own_site AVANT tout
        # fetch de page : jamais fetchées (la devinette ne les construit pas).
        assert not any("instagram.com" in u or "houzz." in u for u in fetched)


def test_find_site_search_cache_shared_across_fiches():
    with Session(_engine()) as s:
        opp1 = _mk_opp(s, source_ref="fiche-a")
        opp2 = _mk_opp(s, source_ref="fiche-b")
        ddg_calls: List[str] = []
        # Site servi sur un domaine NON devinable (les-ateliers-dupont-lyon.fr) :
        # la devinette de domaine échoue -> la découverte passe par DDG, dont le
        # résultat est mis en cache de requête (sitefind:q:) et partagé.
        fetch = _fake_fetch(
            _ddg_html(["https://les-ateliers-dupont-lyon.fr/"]),
            {"les-ateliers-dupont-lyon.fr": _read("site_match.html")}, ddg_calls)

        r1 = find_site(opp1, s, fetch=fetch, today=date(2026, 7, 14))
        r2 = find_site(opp2, s, fetch=fetch, today=date(2026, 7, 14))

        assert r1.verdict == "found" and r2.verdict == "found"
        # Même requête (même nom/ville) -> UNE seule recherche DDG en réseau,
        # le 2e appel lit sitefind:q:* en base.
        assert len(ddg_calls) == 1


def test_find_site_uses_cached_verdict_and_skips_search(monkeypatch):
    with Session(_engine()) as s:
        opp = _mk_opp(s)
        fetch = _fake_fetch(_read("ddg_results.html"),
                            {"atelier-dupont.fr": _read("site_match.html")})
        first = find_site(opp, s, fetch=fetch, today=date(2026, 7, 14))
        assert first.verdict == "found"

        # Un 2e appel, réseau volontairement cassé : doit lire le verdict en
        # cache (sitefind:opp:<id>) sans jamais appeler fetch.
        def _boom(url: str) -> Optional[str]:
            raise AssertionError("fetch ne doit pas etre appele : verdict deja tranche")

        second = find_site(opp, s, fetch=_boom, today=date(2026, 7, 14))
        assert second.from_cache is True
        assert second.verdict == "found"
        assert second.website == "https://atelier-dupont.fr/"


# --- VOIE C : identité par le nom COMPLET du dirigeant (calibrage 2026-07-14) ---


def test_dirigeant_identity_tokens_requires_two_tokens():
    assert _dirigeant_identity_tokens(["Catherine Lassalle, Gérante"]) == \
        ["catherine", "lassalle"]
    # Un patronyme seul (pas de prénom significatif) -> voie C impossible.
    assert _dirigeant_identity_tokens(["Lassalle, Gérante"]) == []
    assert _dirigeant_identity_tokens(None) == []
    # Prénom trop court (< 3 car.) : moins de deux tokens significatifs -> [].
    assert _dirigeant_identity_tokens(["Al Martin, Gérant"]) == []


def test_lock_c_full_name_plus_strong_signal_passes():
    opp = SimpleNamespace(dirigeants=["Catherine Lassalle, Gérante"])
    text = "Catherine Lassalle - Architecte. SIREN 812 345 678, Lyon 69005."
    assert _check_lock_c(opp, text, ["cp", "siren"]) is True


def test_lock_c_requires_strong_geo_immat_not_ville():
    opp = SimpleNamespace(dirigeants=["Catherine Lassalle, Gérante"])
    text = "Catherine Lassalle - Architecte d'intérieur à Lyon."
    # Nom complet présent MAIS aucun signal fort (ville seule) -> refus.
    assert _check_lock_c(opp, text, ["ville"]) is False
    assert _check_lock_c(opp, text, []) is False


def test_lock_c_requires_full_name_present():
    opp = SimpleNamespace(dirigeants=["Catherine Lassalle, Gérante"])
    # Seul le patronyme figure (pas le prénom) -> voie C ne passe pas.
    text = "Cabinet Lassalle. SIREN 812 345 678, Lyon 69005."
    assert _check_lock_c(opp, text, ["cp", "siren"]) is False


# --- Récupération des VRAIS POSITIFS de référence (gate 2026-07-14) -------------
# Chaque vrai positif de référence PERDU par le sur-durcissement devient un test.


def test_reference_fiche1518_catherinelassalle_recovered_via_dirigeant():
    """Fiche 1518 : raison sociale abrégée « CAT LASSALLE » (tokens
    ['cat','lassalle']) qui ne matche NI le contenu (« Catherine ») NI le
    domaine (label fusionné) -> le verrou A échoue. La VOIE C (nom complet du
    dirigeant « Catherine Lassalle » + SIREN/CP sur le site) rattrape le vrai
    site propre. ROUGE avant le calibrage, VERT après."""
    with Session(_engine()) as s:
        opp = _mk_opp(
            s, establishment_name="Cat Lassalle", city="Lyon",
            address="3 rue des Fleurs, 69005 Lyon",
            dirigeants=["Catherine Lassalle, Gérante"], siren="812345678",
            siret="81234567800011",
        )
        fetch = _fake_fetch(
            _ddg_html(["https://catherinelassalle.fr/"]),
            {"catherinelassalle.fr": _read("site_catherine.html")},
        )
        result = find_site(opp, s, fetch=fetch, today=date(2026, 7, 14))

        assert result.verdict == "found"
        assert result.website == "https://catherinelassalle.fr/"
        assert result.inspected[0]["a_pass"] is False  # le nom ne matche PAS
        assert result.inspected[0]["c_pass"] is True    # mais la voie C oui
        assert result.name_signal == "C_dirigeant"


def test_reference_fiche593_emdecoration_found_via_name():
    """Fiche 593 : emdecoration.fr, vrai site propre. Le nom matche (A) et un
    signal fort corrobore (B) -> TROUVÉ (garde de non-régression : le
    durcissement ne doit pas tuer un vrai site propre au nom concordant)."""
    with Session(_engine()) as s:
        opp = _mk_opp(
            s, establishment_name="EM Décoration Intérieur", city="Belfort",
            address="5 faubourg de France, 90000 Belfort",
            dirigeants=["Émilie Martin, Gérante"], siren="903214567",
            siret="90321456700018",
        )
        fetch = _fake_fetch(
            _ddg_html(["https://emdecoration.fr/agence-decoration-interieur-belfort/"]),
            {"emdecoration.fr": _read("site_emdecoration.html")},
        )
        result = find_site(opp, s, fetch=fetch, today=date(2026, 7, 14))

        assert result.verdict == "found"
        # Le site attribué est l'URL candidate rendue par DDG (page profonde
        # légitime du même domaine propre).
        assert _domain(result.website) == "emdecoration.fr"
        assert result.inspected[0]["a_pass"] is True
        assert result.name_signal in ("A1_content", "A2_domain")


def test_reference_fiche1554_pkinterieur_found_via_name():
    """Fiche 1554 : pkinterieur.com, vrai site propre. Nom concordant
    (token 'interieur' + domaine) + SIREN -> TROUVÉ (non-régression)."""
    with Session(_engine()) as s:
        opp = _mk_opp(
            s, establishment_name="PK Intérieur", city="Charbonnières-les-Bains",
            address="8 avenue Lamartine, 69260 Charbonnières-les-Bains",
            dirigeants=["Pauline Klein, Gérante"], siren="851470962",
            siret="85147096200013",
        )
        fetch = _fake_fetch(
            _ddg_html(["https://pkinterieur.com/"]),
            {"pkinterieur.com": _read("site_pkinterieur.html")},
        )
        result = find_site(opp, s, fetch=fetch, today=date(2026, 7, 14))

        assert result.verdict == "found"
        assert result.website == "https://pkinterieur.com/"
        assert result.inspected[0]["a_pass"] is True


# --- RAPPEL 2026-07-17 : devinette de domaine + requêtes citées + cache versionné #
# Les trois vrais sites gatés (593/1518/1554) sont des CONCATÉNATIONS du nom ou du
# dirigeant : la devinette de domaine les retrouve SANS aucun moteur (zéro rate-
# limit). Chaque domaine deviné passe par le MÊME verrou gaté (jamais affaibli).


def _no_engine_fetch(pages_by_domain: Dict[str, str]) -> Callable[[str], Optional[str]]:
    """``fetch`` qui SERT les pages par domaine mais REFUSE tout appel moteur
    (DDG/Bing) : prouve que la devinette de domaine tranche sans aucun moteur."""
    def fetch(url: str) -> Optional[str]:
        low = url.lower()
        if "duckduckgo.com" in low or "bing.com" in low:
            raise AssertionError(f"aucun moteur ne doit être appelé (devinette) : {url}")
        host = urlparse(url).netloc.lower()
        bare = host[4:] if host.startswith("www.") else host
        return pages_by_domain.get(bare)
    return fetch


# --- _guess_domains (pur) --------------------------------------------------------


def test_guess_domains_from_name_join_and_hyphen_crossed_with_tlds():
    opp = SimpleNamespace(establishment_name="EM Décoration Intérieur",
                          dirigeants=["Émilie Martin, Gérante"])
    guesses = _guess_domains(opp)
    # 2 premiers tokens -> emdecoration ; formes join + tiret ; .fr ET .com.
    assert "emdecoration.fr" in guesses
    assert "emdecoration.com" in guesses
    assert "em-decoration.fr" in guesses
    assert len(guesses) <= 10


def test_guess_domains_uses_full_dirigeant_name():
    opp = SimpleNamespace(establishment_name="Cat Lassalle",
                          dirigeants=["Catherine Lassalle, Gérante"])
    guesses = _guess_domains(opp)
    assert "catherinelassalle.fr" in guesses
    assert "catherine-lassalle.fr" in guesses


def test_guess_domains_empty_without_any_signal():
    assert _guess_domains(SimpleNamespace(establishment_name=None, dirigeants=None)) == []
    # Un token unique trop court ne fabrique pas de souche >= 4 caractères.
    assert _guess_domains(SimpleNamespace(establishment_name="Ax", dirigeants=None)) == []


# --- Devinette de domaine : les 3 vrais sites gatés, SANS moteur -----------------


def test_guess_finds_emdecoration_without_any_engine():
    with Session(_engine()) as s:
        opp = _mk_opp(
            s, establishment_name="EM Décoration Intérieur", city="Belfort",
            address="5 faubourg de France, 90000 Belfort",
            dirigeants=["Émilie Martin, Gérante"], siren="903214567",
            siret="90321456700018",
        )
        fetch = _no_engine_fetch({"emdecoration.fr": _read("site_emdecoration.html")})
        result = find_site(opp, s, fetch=fetch, today=date(2026, 7, 14))

        assert result.verdict == "found"
        assert result.website == "https://emdecoration.fr/"
        assert result.queries == []  # AUCUN moteur interrogé
        assert result.inspected[0]["a_pass"] is True


def test_guess_finds_catherinelassalle_via_dirigeant_without_any_engine():
    with Session(_engine()) as s:
        opp = _mk_opp(
            s, establishment_name="Cat Lassalle Intérieurs", city="Lyon",
            address="3 rue des Fleurs, 69005 Lyon",
            dirigeants=["Catherine Lassalle, Gérante"], siren="812345678",
            siret="81234567800011",
        )
        fetch = _no_engine_fetch({"catherinelassalle.fr": _read("site_catherine.html")})
        result = find_site(opp, s, fetch=fetch, today=date(2026, 7, 14))

        assert result.verdict == "found"
        assert result.website == "https://catherinelassalle.fr/"
        assert result.name_signal == "C_dirigeant"
        assert result.inspected[0]["c_pass"] is True
        assert result.queries == []


def test_guess_finds_pkinterieur_com_without_any_engine():
    with Session(_engine()) as s:
        opp = _mk_opp(
            s, establishment_name="PK Intérieur", city="Charbonnières-les-Bains",
            address="8 avenue Lamartine, 69260 Charbonnières-les-Bains",
            dirigeants=["Pauline Klein, Gérante"], siren="851470962",
            siret="85147096200013",
        )
        fetch = _no_engine_fetch({"pkinterieur.com": _read("site_pkinterieur.html")})
        result = find_site(opp, s, fetch=fetch, today=date(2026, 7, 14))

        assert result.verdict == "found"
        assert result.website == "https://pkinterieur.com/"  # .com deviné, pas .fr
        assert result.queries == []


def test_guess_domain_homonym_is_refused_by_the_same_lock():
    """Un domaine DEVINÉ vivant appartenant à un homonyme (autre ville, aucun
    signal fort) ne passe PAS le verrou : jamais attribué. La devinette
    n'affaiblit rien (VIDE > FAUX). Moteurs muets ici -> reste réessayable."""
    with Session(_engine()) as s:
        opp = _mk_opp(s)  # Atelier Dupont, Lyon -> devine atelierdupont.fr

        def fetch(url: str) -> Optional[str]:
            low = url.lower()
            if "duckduckgo.com" in low or "bing.com" in low:
                return None  # moteurs muets (le domaine deviné a déjà été rejeté)
            host = urlparse(url).netloc.lower()
            bare = host[4:] if host.startswith("www.") else host
            return {"atelierdupont.fr": _read("site_homonym_othercity.html")}.get(bare)

        result = find_site(opp, s, fetch=fetch, today=date(2026, 7, 14))
        assert result.website is None
        assert result.verdict == "locked_out"
        assert result.inspected[0]["a_pass"] is True
        assert result.inspected[0]["b_signals"] == []


# --- Requêtes citées + cache de verdict versionné --------------------------------


def test_build_queries_quotes_full_name_when_two_significant_tokens():
    opp = SimpleNamespace(establishment_name="EM Décoration Intérieur",
                          city="Belfort", address="", dirigeants=None)
    queries = _build_queries(opp)
    assert queries[0] == '"EM Décoration Intérieur" Belfort'
    # Les variantes non citées restent en repli.
    assert any(not q.startswith('"') for q in queries)


def test_build_queries_no_quote_when_single_significant_token():
    opp = SimpleNamespace(establishment_name="Atelier Dupont", city="Lyon",
                          address="", dirigeants=["Chiara Rossi, Gérante"])
    assert not any(q.startswith('"') for q in _build_queries(opp))


def test_verdict_handle_is_versioned():
    assert _verdict_handle(SimpleNamespace(id=593, siren="903214567")) == \
        f"sitefind:opp:v{_ENGINE_VERSION}:593"
    # Repli SIREN aussi versionné quand l'id est absent.
    assert _verdict_handle(SimpleNamespace(id=None, siren="903214567")) == \
        f"sitefind:siren:v{_ENGINE_VERSION}:903214567"
