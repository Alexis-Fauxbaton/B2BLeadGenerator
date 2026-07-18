# backend/tests/test_mon_architecte_interieur_connector.py
"""Connecteur Mon Architecte d'Intérieur (A2) — parsing PUR sur snapshots HTML
réels (tests/fixtures/mon_architecte_interieur/, récupérés poliment le
2026-07-17, throttle >= 2,5 s). Aucun réseau : http_fetch injecté."""
from pathlib import Path

import pytest

from app.ingestion.annuaires.mon_architecte_interieur import (
    MonArchitecteInterieurConnector,
    _is_belgian_address,
    _is_hors_cible_maitre_oeuvre,
    normalize_phone_fr,
    parse_fiche,
    parse_list_page,
    parse_total,
)

FIXTURES = Path(__file__).parent / "fixtures" / "mon_architecte_interieur"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


LIST_P1 = _load("list_p1.html")
LIST_P2 = _load("list_p2.html")
LIST_P3 = _load("list_p3.html")
FICHE_ARCHISUIVI = _load("fiche_344_archisuivi.html")  # fiche complète (tél + site)
FICHE_ARCHI_IN = _load("fiche_100_archi_in_belgique.html")  # hors-cible (Belgique)
FICHE_EMILIE = _load("fiche_231_emilie_bouaziz_no_address.html")  # sans adresse


def test_parse_list_page_extracts_rows():
    rows = parse_list_page(LIST_P1)
    assert len(rows) == 10
    ids = {r["listing_id"] for r in rows}
    assert {"100", "344", "313", "160", "231", "376", "341", "335", "281", "120"} == ids
    archisuivi = next(r for r in rows if r["listing_id"] == "344")
    assert archisuivi["listing_url"] == (
        "https://www.mon-architecte-interieur.com/annuaire/"
        "archisuivi-architecte-dinterieur-a-nice/"
    )
    assert "ARCHISUIVI" in archisuivi["title"]


def test_parse_list_page_all_three_real_pages():
    # Pagination réelle complète : 10 + 10 + 4 = 24, aligné sur le badge catégorie.
    assert len(parse_list_page(LIST_P1)) == 10
    assert len(parse_list_page(LIST_P2)) == 10
    assert len(parse_list_page(LIST_P3)) == 4


def test_parse_total():
    assert parse_total(LIST_P1) == 24
    assert parse_total("<div>pas de badge</div>") is None


def test_parse_fiche_complete_target():
    f = parse_fiche(FICHE_ARCHISUIVI, "344")
    assert f is not None
    assert f["name"] == "ARCHISUIVI, architecte d’intérieur à Nice"
    assert f["city"] == "Nice"
    assert "06200" in f["address"] and "NICE" in f["address"]
    assert f["phone"] == "04 81 68 35 32"
    assert f["website"] == "https://www.archisuivi.com/"


def test_parse_fiche_belgian_address_is_dropped():
    # Garde hors-cible (sonde) : ARCHI-IN (Andenne, Belgique) -> écarté.
    assert parse_fiche(FICHE_ARCHI_IN, "100") is None


def test_parse_fiche_without_address_stays_valid():
    # Régression réelle (fiche 231) : pas de champ "Adresse" du tout (VIDE >
    # FAUX -- champ absent, jamais un faux positif Belgique) ; "Ville" seule
    # suffit, la fiche reste gardée.
    f = parse_fiche(FICHE_EMILIE, "231")
    assert f is not None
    assert f["name"] == "Architecte d’intérieur à Lille, Emilie Bouaziz"
    assert f["address"] == ""
    assert f["city"] == "Lille"
    assert f["phone"] == "03 66 88 33 59"


def test_parse_fiche_missing_h1_returns_none():
    assert parse_fiche("<div>page cassée</div>", "0") is None


# --- Défaut qualité #2 (revue Alexis, 2026-07-18) : garde hors-cible « maître
# d'œuvre » — cas réel fiche #6785 « Maître d'oeuvre Vigneux-de-Bretagne --
# Guillaume Clouet », site clouet-maitre-oeuvre.com. Structure synthétique
# (mêmes classes wpbdp-field-* que les fiches réelles ci-dessus).

def _fiche_html(name: str, presentation: str = "", adresse: str = "Nice") -> str:
    return f"""
<div class="listing-title"><h1>{name}</h1></div>
<div class="wpbdp-field-adresse wpbdp-field"><div class="value">{adresse}</div></div>
<div class="wpbdp-field-ville wpbdp-field"><div class="value">Nice</div></div>
<div class="wpbdp-field-numero_de_telephone_ wpbdp-field"><div class="value">04 00 00 00 00</div></div>
<div class="wpbdp-field-presentation_de_larchitecte wpbdp-field">
  <div class="value">{presentation}</div>
</div>
"""


def test_parse_fiche_maitre_oeuvre_title_only_is_dropped():
    # Cas réel : titre 100% "maître d'oeuvre", aucune mention archi/décoration.
    html = _fiche_html("Maître d'oeuvre Vigneux-de-Bretagne – Guillaume Clouet")
    assert parse_fiche(html, "149") is None


def test_parse_fiche_maitrise_oeuvre_in_description_without_interior_is_dropped():
    html = _fiche_html(
        "Guillaume Clouet",
        presentation="Nous proposons une prestation complète de maîtrise d'œuvre "
                     "pour vos projets de construction et de rénovation.",
    )
    assert parse_fiche(html, "150") is None


def test_parse_fiche_bureau_etudes_is_dropped():
    html = _fiche_html("Bureau d'études BTP Ouest")
    assert parse_fiche(html, "151") is None


def test_parse_fiche_architecte_mentioning_maitrise_oeuvre_as_service_stays_valid():
    # Régression : une VRAIE fiche archi d'intérieur qui propose AUSSI de la
    # maîtrise d'œuvre (service complémentaire) ne doit PAS être écartée --
    # la mention "architecte"/"décorat" suffit à la garder (VIDE > FAUX).
    html = _fiche_html(
        "ARCHISUIVI, architecte d'intérieur à Nice",
        presentation="Cabinet d'architecture d'intérieur proposant également une "
                     "prestation de maîtrise d'œuvre pour le suivi de chantier.",
    )
    f = parse_fiche(html, "344")
    assert f is not None
    assert f["name"] == "ARCHISUIVI, architecte d'intérieur à Nice"


@pytest.mark.parametrize("name,description,expected", [
    ("Maître d'oeuvre Vigneux-de-Bretagne – Guillaume Clouet", "", True),
    ("MAITRE D'OEUVRE DUPONT", "", True),           # accent-insensible + majuscules
    ("Constructeur de maisons individuelles", "", True),
    ("Guillaume Clouet", "Bureau d'études structure bâtiment", True),
    ("ARCHISUIVI, architecte d'intérieur à Nice", "", False),   # pas de garde MOE
    ("Studio Déco", "Décoratrice d'intérieur", False),
    (
        "Cabinet Dupont",
        "Architecte proposant aussi de la maîtrise d'œuvre",
        False,
    ),  # service parmi d'autres -> gardé
    ("", "", False),
])
def test_is_hors_cible_maitre_oeuvre(name, description, expected):
    assert _is_hors_cible_maitre_oeuvre(name, description) is expected


@pytest.mark.parametrize("address,expected", [
    ("18 Rue Brun, 5300 ANDENNE BELGIQUE", True),   # mention explicite
    ("Route de Hannut N°47 Boite 3 5004 NAMUR", True),   # code postal 4 chiffres
    ("14, rue Ferdinand Nicolay, 4000 LIEGE", True),     # code postal 4 chiffres
    ("96 corniche fleurie 06200 – NICE", False),         # code postal FR 5 chiffres
    ("2 Rue du Docteur Burnet 27200 VERNON", False),
    ("", False),          # VIDE > FAUX : adresse absente = jamais exclue
    ("Lille", False),     # pas de code postal identifiable = jamais exclue
])
def test_is_belgian_address(address, expected):
    assert _is_belgian_address(address) is expected


@pytest.mark.parametrize("raw,expected", [
    ("04 81 68 35 32", "04 81 68 35 32"),
    ("03 28 09 94 00", "03 28 09 94 00"),
    ("0977215828", "09 77 21 58 28"),
    ("+33 6 62 13 12 59", "06 62 13 12 59"),
    ("081.39.50.36", None),      # belge (9 chiffres) -- jamais inventé à 10
    ("+32 81 39 50 32", None),   # belge international
    ("", None),
    (None, None),
])
def test_normalize_phone_fr(raw, expected):
    assert normalize_phone_fr(raw) == expected


def test_connector_fetch_paginates_and_drops_belgian():
    pages = {
        "https://www.mon-architecte-interieur.com/annuaire/": LIST_P1,
        "https://www.mon-architecte-interieur.com/annuaire/"
        "archisuivi-architecte-dinterieur-a-nice/": FICHE_ARCHISUIVI,
        "https://www.mon-architecte-interieur.com/annuaire/archi-in/": FICHE_ARCHI_IN,
    }
    calls = []

    def fake(url):
        calls.append(url)
        return pages.get(url)

    conn = MonArchitecteInterieurConnector(http_fetch=fake)
    records = conn.fetch(limit=100, max_pages=1)
    ids = {r["listing_id"] for r in records}
    # Seule ARCHISUIVI (id 344) a une fiche injectée ET n'est pas belge ;
    # ARCHI-IN (id 100) a une fiche injectée mais est écartée (Belgique) ; les
    # 8 autres lignes de la page n'ont pas de fiche injectée -> ignorées
    # (http_fetch fail-soft renvoie None).
    assert ids == {"344"}
    assert conn.last_total_count == 24  # badge catégorie, pas juste len(rows)
    # Throttle : jamais deux fois la même URL fetchée.
    assert len(calls) == len(set(calls))


def test_connector_fetch_bounded_by_limit():
    conn = MonArchitecteInterieurConnector(
        http_fetch=lambda u: LIST_P1 if u.endswith("/annuaire/") else FICHE_ARCHISUIVI)
    records = conn.fetch(limit=3, max_pages=1)
    assert len(records) <= 3


def test_connector_fetch_stops_when_http_fetch_returns_none():
    conn = MonArchitecteInterieurConnector(http_fetch=lambda u: None)
    records = conn.fetch(limit=100, max_pages=5)
    assert records == []
    assert conn.last_total_count == 0


def test_to_candidates_maps_architecte_annuaire():
    conn = MonArchitecteInterieurConnector(http_fetch=lambda u: None)
    cand = conn.to_candidates([{
        "listing_id": "344", "name": "ARCHISUIVI, architecte d’intérieur à Nice",
        "address": "96 corniche fleurie 06200 – NICE", "city": "Nice",
        "phone": "04 81 68 35 32", "website": "https://www.archisuivi.com/",
        "fiche_url": "https://www.mon-architecte-interieur.com/annuaire/"
                     "archisuivi-architecte-dinterieur-a-nice/",
    }])[0]
    assert cand.source == "annuaire" and cand.source_ref == "monarchitecteinterieur:344"
    assert cand.population == "architecte"
    assert cand.lifecycle_label == "studio_actif"
    assert cand.main_signal == "prescripteur actif"
    assert cand.establishment_type == "architecte d'intérieur"
    assert cand.establishment_name == "ARCHISUIVI, architecte d’intérieur à Nice"
    assert cand.city == "Nice"
    assert cand.website == "https://www.archisuivi.com/"
    assert "annuaire monarchitecteinterieur" in cand.secondary_signals
    assert cand.email is None  # pas d'email en clair (sonde)
    assert cand.decision_maker is None  # pas de nom de personne structuré fiable
    # Régression : le téléphone doit être reporté dans raw['phone'] -- seul
    # chemin lu par pipeline._process_candidate pour remplir Opportunity.phone
    # (même contrat que CFAI/UFDI/Annuaire Décoration/Places).
    assert cand.raw.get("phone") == "04 81 68 35 32"


def test_to_candidates_without_phone_leaves_raw_falsy():
    conn = MonArchitecteInterieurConnector(http_fetch=lambda u: None)
    cand = conn.to_candidates([{
        "listing_id": "231", "name": "Emilie Bouaziz", "address": "", "city": "Lille",
        "phone": None, "website": "https://www.emilie-bouaziz.com/design.php",
        "fiche_url": "x",
    }])[0]
    assert not cand.raw.get("phone")
    assert cand.email is None
