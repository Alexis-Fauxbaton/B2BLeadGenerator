"""Tests ADVERSES du moteur de découverte de site (Brique A).

But : CASSER le verrou d'identité. Chaque test cherche un FAUX POSITIF (site
attribué à tort, doctrine VIDE > FAUX violée) ou un plantage sur entrée
dégénérée. Zéro réseau (``fetch`` factice)."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Dict, List, Optional
from urllib.parse import quote, urlparse

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.ingestion.enrichment import site_finder
from app.ingestion.enrichment.own_site import is_directory, own_site
from app.ingestion.enrichment.site_finder import (
    _check_lock_c,
    _corroboration_ok,
    _domain_matches_name,
    find_site,
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


def _fake_fetch(
    ddg_html: str, pages_by_domain: Dict[str, str], calls: Optional[List[str]] = None,
) -> Callable[[str], Optional[str]]:
    def fetch(url: str) -> Optional[str]:
        if calls is not None:
            calls.append(url)
        if "html.duckduckgo.com" in url:
            return ddg_html
        host = urlparse(url).netloc.lower()
        bare = host[4:] if host.startswith("www.") else host
        return pages_by_domain.get(bare)
    return fetch


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
# 1. ANNUAIRE (pappers.fr) contenant nom+ville+SIREN de la fiche.             #
#    Doctrine : ce n'est PAS le site PROPRE du lead -> doit être VIDE.         #
# --------------------------------------------------------------------------- #

def test_directory_pappers_with_name_city_siren_must_not_be_attributed():
    with Session(_engine()) as s:
        opp = _opp()
        pappers_html = (
            "<title>Atelier Dupont (123 456 789) - Pappers</title>"
            "<h1>Atelier Dupont</h1>"
            "<body>SIREN 123 456 789 - LYON 69001. Gérante Chiara Rossi. "
            "Annuaire des entreprises Pappers.</body>"
        )
        fetch = _fake_fetch(
            _ddg_html(["https://www.pappers.fr/entreprise/atelier-dupont-123456789"]),
            {"pappers.fr": pappers_html},
        )
        result = find_site(opp, s, fetch=fetch, today=date(2026, 7, 14))
        # Un annuaire n'est jamais le site propre du lead.
        assert result.website is None, (
            f"FAUX POSITIF : annuaire attribué comme site propre -> {result.website}")
        assert "pappers.fr" not in (result.website or "")


def test_directory_societe_com_must_not_be_attributed():
    with Session(_engine()) as s:
        opp = _opp()
        societe_html = (
            "<title>ATELIER DUPONT - Lyon (69001) - societe.com</title>"
            "<h1>Atelier Dupont</h1><body>SIREN 123456789 Lyon 69001</body>"
        )
        fetch = _fake_fetch(
            _ddg_html(["https://www.societe.com/societe/atelier-dupont-123456789.html"]),
            {"societe.com": societe_html},
        )
        result = find_site(opp, s, fetch=fetch, today=date(2026, 7, 14))
        assert result.website is None, (
            f"FAUX POSITIF : societe.com attribué -> {result.website}")


# --------------------------------------------------------------------------- #
# 2. HOMONYME EXACT même nom, même CP (le PIRE cas).                          #
# --------------------------------------------------------------------------- #

def test_exact_homonym_same_name_same_cp_is_worst_case():
    with Session(_engine()) as s:
        opp = _opp()
        # Autre société, MÊME nom "Atelier Dupont", MÊME CP 69001, mais SIREN
        # et dirigeant différents. Aucun signal DISCRIMINANT ne les sépare.
        homonym_html = (
            "<title>Atelier Dupont - Lyon</title><h1>Atelier Dupont</h1>"
            "<body>Bienvenue a Lyon 69001. SIREN 999888777. "
            "Dirigeant : Marc Durand.</body>"
        )
        fetch = _fake_fetch(
            _ddg_html(["https://atelier-dupont-lyon.fr/"]),
            {"atelier-dupont-lyon.fr": homonym_html},
        )
        result = find_site(opp, s, fetch=fetch, today=date(2026, 7, 14))
        # On DOCUMENTE le comportement réel (attendu : found via A+B-cp/ville,
        # alors que c'est une AUTRE société -> faux positif structurel).
        print("EXACT_HOMONYM verdict=", result.verdict, "website=", result.website,
              "corr=", result.corroboration)


# --------------------------------------------------------------------------- #
# 3. NOM GÉNÉRIQUE "Studio Déco" à Paris.                                     #
# --------------------------------------------------------------------------- #

def test_generic_name_studio_deco_paris():
    with Session(_engine()) as s:
        opp = _opp(establishment_name="Studio Déco", city="Paris",
                   address="5 rue de Rivoli, 75001 Paris", siren="123456789",
                   siret="12345678900012", dirigeants=["Jean Martin, Gérant"])
        # Site d'un AUTRE studio déco parisien quelconque.
        other_html = (
            "<title>Studio Deco - Decoration a Paris</title>"
            "<h1>Studio Deco Paris</h1>"
            "<body>Notre studio a Paris 75001. Decoration interieure.</body>"
        )
        fetch = _fake_fetch(
            _ddg_html(["https://studio-deco-paris.fr/"]),
            {"studio-deco-paris.fr": other_html},
        )
        result = find_site(opp, s, fetch=fetch, today=date(2026, 7, 14))
        print("GENERIC verdict=", result.verdict, "website=", result.website,
              "signal=", result.name_signal, "corr=", result.corroboration)


# --------------------------------------------------------------------------- #
# 4. DDG vide / malformé -> recherche NON SERVIE (aucun moteur), pas de plantage.
#    Bing (repli) est muet aussi (fake ne le sert pas) -> search_unavailable.   #
# --------------------------------------------------------------------------- #

def test_ddg_empty_or_malformed_no_crash():
    with Session(_engine()) as s:
        opp = _opp()
        for bad in ("", "<html><body>rien</body></html>", "<a class=result__a>x",
                    "<<<>>> not html at all"):
            fetch = _fake_fetch(bad, {})
            result = find_site(opp, s, fetch=fetch, today=date(2026, 7, 14),
                               )
            # Aucun moteur ne rend de résultat -> recherche non servie (réessayable),
            # PAS "no_candidate" (qui signifierait qu'un moteur a répondu à vide).
            assert result.verdict == "search_unavailable"
            assert result.website is None


# --------------------------------------------------------------------------- #
# 5. TIMEOUT / réseau mort (fetch -> None partout) : fail-soft, RÉESSAYABLE.   #
# --------------------------------------------------------------------------- #

def test_network_timeout_fail_soft():
    with Session(_engine()) as s:
        opp = _opp()

        def dead(url: str) -> Optional[str]:
            return None  # simule timeout / statut != 200 / MIME non-html

        result = find_site(opp, s, fetch=dead, today=date(2026, 7, 14))
        # Tous les moteurs muets -> recherche non servie (réessayable), jamais
        # figée en "no_candidate".
        assert result.verdict == "search_unavailable"
        assert result.website is None


# --------------------------------------------------------------------------- #
# 6. Fiche SANS ville ni dirigeant ni siren/siret : B impossible -> VIDE.     #
# --------------------------------------------------------------------------- #

def test_opp_without_any_corroboration_field_yields_empty():
    with Session(_engine()) as s:
        opp = SimpleNamespace(
            id=42, establishment_name="Atelier Dupont", city=None,
            address=None, dirigeants=None, siren=None, siret=None,
        )
        # Le site matche le NOM (A ok) mais AUCUN signal B ne peut exister.
        match_html = ("<title>Atelier Dupont</title><h1>Atelier Dupont</h1>"
                      "<body>Architecte d'interieur.</body>")
        fetch = _fake_fetch(
            _ddg_html(["https://atelier-dupont.fr/"]),
            {"atelier-dupont.fr": match_html},
        )
        result = find_site(opp, s, fetch=fetch, today=date(2026, 7, 14))
        assert result.website is None, "A seul ne suffit jamais (B obligatoire)"
        assert result.verdict == "locked_out"


def test_opp_all_none_does_not_crash():
    with Session(_engine()) as s:
        opp = SimpleNamespace(
            id=None, establishment_name=None, city=None, address=None,
            dirigeants=None, siren=None, siret=None,
        )
        fetch = _fake_fetch(_ddg_html([]), {})
        result = find_site(opp, s, fetch=fetch, today=date(2026, 7, 14))
        assert result.website is None
        assert result.verdict in ("no_candidate", "locked_out", "search_unavailable", "error")


# --------------------------------------------------------------------------- #
# 7. Cache : 2e appel = ZÉRO réseau (vérification dure du compteur).          #
# --------------------------------------------------------------------------- #

def test_cache_second_call_zero_network():
    with Session(_engine()) as s:
        opp = _opp()
        calls: List[str] = []
        fetch = _fake_fetch(
            _ddg_html(["https://atelier-dupont.fr/"]),
            {"atelier-dupont.fr": ("<title>Atelier Dupont Lyon</title>"
                                   "<body>Lyon 69001 SIREN 123456789</body>")},
            calls,
        )
        find_site(opp, s, fetch=fetch, today=date(2026, 7, 14))
        n_after_first = len(calls)
        find_site(opp, s, fetch=fetch, today=date(2026, 7, 14))
        assert len(calls) == n_after_first, "2e appel doit être 0 réseau (cache verdict)"


# --------------------------------------------------------------------------- #
# 8. Transient DDG failure ne doit PAS empoisonner le cache (+2 mois).        #
# --------------------------------------------------------------------------- #

def test_transient_ddg_failure_is_not_cached_as_no_candidate():
    """Un échec réseau TRANSITOIRE (fetch -> None) ne doit pas verrouiller la
    fiche en "no_candidate" pendant la fenêtre de revisite : dès que le
    réseau est rétabli, un nouvel appel (même jour, donc hors fenêtre de
    revisite si le verdict avait été mis en cache) doit re-chercher."""
    with Session(_engine()) as s:
        opp = _opp()

        def dead(url: str) -> Optional[str]:
            return None

        r1 = find_site(opp, s, fetch=dead, today=date(2026, 7, 14))
        # Réseau muet -> search_unavailable (jamais mis en cache, réessayable).
        assert r1.verdict == "search_unavailable"
        assert r1.from_cache is False

        # Réseau RÉTABLI, même jour : le verdict "no_candidate" transitoire
        # ne doit PAS avoir été mis en cache -> ce 2e appel doit re-chercher
        # (pas de court-circuit "from_cache") et trouver le bon site.
        good = _fake_fetch(
            _ddg_html(["https://atelier-dupont.fr/"]),
            {"atelier-dupont.fr": ("<title>Atelier Dupont Lyon</title>"
                                   "<body>Lyon 69001 SIREN 123456789</body>")},
        )
        r2 = find_site(opp, s, fetch=good, today=date(2026, 7, 14))
        assert r2.from_cache is False, (
            "verdict transitoire empoisonné : la fiche a été verrouillée en cache")
        assert r2.verdict == "found"
        assert r2.website == "https://atelier-dupont.fr/"


# --------------------------------------------------------------------------- #
# DURCISSEMENTS POST-GATE DU 2026-07-14 — un test adverse par faux positif    #
# réellement observé lors du gate (6 attributions fausses sur 15).            #
# --------------------------------------------------------------------------- #

# 9. AGRÉGATEURS SIRENE acceptés à tort comme sites propres. Leurs URLs de
#    fiche générée republient ville/CP/SIREN de N'IMPORTE QUELLE entreprise ->
#    B est satisfaite trivialement à tort. Doivent être écartés en amont.

@pytest.mark.parametrize("url", [
    "https://www.118000.fr/e_C0101327518",
    "https://www.le-site-de.com/novea-home-coueron_33582.html",
    "https://prosmaison.fr/entreprise-43435829700076",
    "https://hexagone-architecture.fr/devis/atelier-dupont",
])
def test_sirene_aggregators_are_not_own_sites(url):
    assert own_site(url) is None, f"agrégateur accepté comme site propre : {url}"


def test_find_site_sirene_aggregators_not_attributed():
    with Session(_engine()) as s:
        opp = _opp()
        # Chaque agrégateur porte nom+ville+CP+SIREN EXACTS de la fiche (B
        # trivialement vraie) : doivent être filtrés AVANT tout fetch de page.
        agg_html = ("<title>Atelier Dupont Lyon 69001 SIREN 123456789</title>"
                    "<h1>Atelier Dupont</h1><body>Lyon 69001 SIREN 123 456 789 "
                    "Gérante Chiara Rossi</body>")
        fetch = _fake_fetch(
            _ddg_html([
                "https://www.118000.fr/e_C0101327518",
                "https://www.le-site-de.com/atelier-dupont-lyon_33582.html",
                "https://prosmaison.fr/entreprise-43435829700076",
                "https://hexagone-architecture.fr/pro/atelier-dupont",
            ]),
            {"118000.fr": agg_html, "le-site-de.com": agg_html,
             "prosmaison.fr": agg_html, "hexagone-architecture.fr": agg_html},
        )
        result = find_site(opp, s, fetch=fetch, today=date(2026, 7, 14))
        assert result.website is None, (
            f"FAUX POSITIF : agrégateur SIRENE attribué -> {result.website}")
        assert result.candidates == []
        assert result.verdict == "no_candidate"


# 10. Corroboration "VILLE SEULE" : DAMSO INTERIEURS (Lyon) a matché la
#     billetterie d'un concert du rappeur Damso à Lyon (nom OK + ville OK, rien
#     d'autre). "ville" seule ne corrobore plus.

def test_corroboration_ville_alone_is_insufficient():
    assert _corroboration_ok(["ville"]) is False
    assert _corroboration_ok([]) is False
    assert _corroboration_ok(["cp"]) is True
    assert _corroboration_ok(["dirigeant"]) is True
    assert _corroboration_ok(["siren"]) is True
    assert _corroboration_ok(["siret"]) is True
    assert _corroboration_ok(["ville", "cp"]) is True


def test_ville_only_corroboration_is_refused_damso():
    with Session(_engine()) as s:
        opp = _opp(establishment_name="DAMSO INTERIEURS", city="Lyon",
                   address="10 rue Centrale, 69002 Lyon",
                   dirigeants=["Sophie Bernard, Gérante"], siren="321654987",
                   siret="32165498700011")
        fetch = _fake_fetch(
            _ddg_html(["https://www.olvallee.fr/evenement/damso-lyon-2026/"]),
            {"olvallee.fr": _read("site_ville_only.html")},
        )
        result = find_site(opp, s, fetch=fetch, today=date(2026, 7, 14))
        assert result.website is None, "ville seule ne corrobore plus (VIDE > FAUX)"
        assert result.verdict == "locked_out"
        assert result.inspected[0]["a_pass"] is True
        assert result.inspected[0]["b_signals"] == ["ville"]


# 11. Match de NOM laxiste : ARCHIVEST a matché archives.territoiredebelfort.fr
#     (coïncidence lexicale par sous-chaîne : "archivest" dans "archives...t").

def test_domain_matches_name_rejects_substring_coincidence():
    # "archivest" NE matche PAS "archives.territoiredebelfort.fr" (sous-chaîne).
    assert _domain_matches_name("archives.territoiredebelfort.fr", ["archivest"]) is False
    # Un vrai SEGMENT du domaine continue de matcher.
    assert _domain_matches_name("atelier-dupont.fr", ["dupont"]) is True


def test_name_substring_coincidence_is_refused_archivest():
    with Session(_engine()) as s:
        opp = _opp(establishment_name="ARCHIVEST", city="Belfort",
                   address="1 place Corbis, 90000 Belfort",
                   dirigeants=["Luc Moreau, Président"], siren="741852963",
                   siret="74185296300017")
        fetch = _fake_fetch(
            _ddg_html(["https://archives.territoiredebelfort.fr/"]),
            {"archives.territoiredebelfort.fr": _read("site_archives_public.html")},
        )
        result = find_site(opp, s, fetch=fetch, today=date(2026, 7, 14))
        assert result.website is None, "coïncidence lexicale (archivest != archives)"
        assert result.verdict == "locked_out"
        assert result.inspected[0]["a_pass"] is False


# 12. Signal de NOM validé sur la HOME RACINE, pas sur la page profonde DDG :
#     un agrégateur PAS encore listé a une fiche profonde riche (nom+SIREN) mais
#     une home racine GÉNÉRIQUE qui ne matche jamais le nom du studio.

def test_aggregator_generic_root_home_defeats_deep_page_name_match():
    with Session(_engine()) as s:
        opp = _opp()
        generic_home = _read("agg_home_generic.html")
        deep_fiche = _read("agg_fiche_deep.html")

        def fetch(url: str) -> Optional[str]:
            if "html.duckduckgo.com" in url:
                return _ddg_html(["https://trouver-un-pro.fr/fiche/atelier-dupont-lyon"])
            p = urlparse(url)
            host = p.netloc.lower()
            bare = host[4:] if host.startswith("www.") else host
            if bare != "trouver-un-pro.fr":
                return None
            return deep_fiche if "/fiche/" in p.path else generic_home

        result = find_site(opp, s, fetch=fetch, today=date(2026, 7, 14))
        assert result.website is None, "home racine générique -> pas d'attribution"
        assert result.verdict == "locked_out"
        assert result.inspected[0]["a_pass"] is False


# 13. Attribution UNIQUEMENT si la home du domaine racine a répondu (fix #5).

def test_dead_root_home_is_not_attributed():
    with Session(_engine()) as s:
        opp = _opp()

        def fetch(url: str) -> Optional[str]:
            if "html.duckduckgo.com" in url:
                return _ddg_html(["https://atelier-dupont.fr/"])
            return None  # home racine morte (timeout / 404 / MIME)

        result = find_site(opp, s, fetch=fetch, today=date(2026, 7, 14))
        assert result.website is None
        assert result.verdict == "locked_out"
        assert result.inspected[0]["home_alive"] is False
        assert result.inspected[0]["a_pass"] is False


# --------------------------------------------------------------------------- #
# CALIBRAGE DU 2026-07-14 — la VOIE C (nom complet du dirigeant + signal fort  #
# géo/immat) rattrape les noms abrégés SANS rouvrir la porte aux faux.         #
# --------------------------------------------------------------------------- #

# 14. Voie C : le nom complet du dirigeant SEUL (sans cp/siren/siret) ne suffit
#     jamais. Un simple homonyme de personne + coïncidence de ville resterait
#     refusé (VIDE > FAUX).

def test_lock_c_full_name_without_strong_signal_is_refused():
    assert _check_lock_c(
        SimpleNamespace(dirigeants=["Jean Dupont, Gérant"]),
        "Jean Dupont vous accueille à Lyon.", ["ville"]) is False


def test_find_site_dirigeant_name_but_only_ville_is_refused():
    """Home citant le nom complet du dirigeant MAIS aucun signal fort (ni cp,
    ni siren, ni siret) : ni la voie A (nom social absent) ni la voie C ne
    passent -> VIDE > FAUX."""
    with Session(_engine()) as s:
        opp = _opp(establishment_name="Studio ZZ", city="Lyon",
                   address="1 rue X, 69001 Lyon",
                   dirigeants=["Chiara Rossi, Gérante"], siren=None, siret=None)
        home = ("<title>Bienvenue chez Chiara Rossi</title>"
                "<h1>Chiara Rossi</h1><body>Basée à Lyon, sans plus.</body>")
        fetch = _fake_fetch(_ddg_html(["https://chiara-rossi-home.fr/"]),
                            {"chiara-rossi-home.fr": home})
        result = find_site(opp, s, fetch=fetch, today=date(2026, 7, 14))
        assert result.website is None
        assert result.verdict == "locked_out"
        assert result.inspected[0]["c_pass"] is False


# 15. Voie C ne ressuscite AUCUN faux de référence : la billetterie Damso et les
#     archives de Belfort ne citent pas le nom complet du dirigeant de la fiche.

def test_lock_c_does_not_resurrect_damso_or_archives():
    damso_opp = _opp(establishment_name="DAMSO INTERIEURS", city="Lyon",
                     dirigeants=["Sophie Bernard, Gérante"], siren="321654987",
                     siret="32165498700011")
    assert _check_lock_c(damso_opp, _read("site_ville_only.html"),
                         ["ville"]) is False
    arch_opp = _opp(establishment_name="ARCHIVEST", city="Belfort",
                    dirigeants=["Luc Moreau, Président"], siren="741852963",
                    siret="74185296300017")
    # Même en imaginant un CP présent, le nom complet du dirigeant est ABSENT.
    assert _check_lock_c(arch_opp, _read("site_archives_public.html"),
                         ["cp"]) is False


# 16. Rééquilibrage de DIRECTORY_URL_RE : les fiches d'agrégateur restent
#     détectées, mais un slug daté légitime (année à 4 chiffres) ne l'est plus.

@pytest.mark.parametrize("url", [
    "https://www.118000.fr/e_C0101327518",                       # /e_C<id> ancré
    "https://www.le-site-de.com/novea-home-coueron_33582.html",  # id >= 5 chiffres
    "https://www.le-site-de.com/ombelle-interieur-pessac_46787.html",
    "https://prosmaison.fr/entreprise-43435829700076",           # /entreprise-<SIRET>
])
def test_directory_url_re_still_flags_aggregator_fiches(url):
    assert is_directory(url) is True, f"fiche d'agrégateur non détectée : {url}"


@pytest.mark.parametrize("url", [
    "https://mon-studio-archi.fr/blog/renovation-cuisine_2024.html",  # année, 4 chiffres
    "https://atelier-belle-deco.fr/projets/loft-lyon_2023.html",
])
def test_directory_url_re_spares_legit_dated_pages(url):
    # Un slug daté (année 4 chiffres) sur un domaine PROPRE n'est plus pris pour
    # une fiche d'annuaire -> le site reste éligible.
    assert is_directory(url) is False, f"page datée légitime exclue à tort : {url}"
    assert own_site(url) == url
