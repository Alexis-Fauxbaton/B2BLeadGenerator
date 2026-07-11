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
