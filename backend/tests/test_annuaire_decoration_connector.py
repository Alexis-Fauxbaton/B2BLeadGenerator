# backend/tests/test_annuaire_decoration_connector.py
"""Connecteur Annuaire Décoration (A2) — parsing PUR sur snapshots HTML réels
(tests/fixtures/annuaire_decoration/, récupérés poliment le 2026-07-17,
throttle >= 2,5 s). Aucun réseau : http_fetch injecté."""
from pathlib import Path

import pytest

from app.ingestion.annuaires.annuaire_decoration import (
    AnnuaireDecorationConnector,
    normalize_phone_fr,
    parse_fiche,
    parse_list_page,
    parse_max_page,
)

FIXTURES = Path(__file__).parent / "fixtures" / "annuaire_decoration"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


LIST_ARCHITECTE_P1 = _load("list_architecte_p1.html")
LIST_DECORATEUR_P1 = _load("list_decorateur_p1.html")
FICHE_ARCHITECTE_429 = _load("fiche_architecte_429.html")  # fiche complète (tél + site)
FICHE_ARCHITECTE_12 = _load("fiche_architecte_12_no_contact.html")  # sans bloc société
FICHE_DECORATEUR_452 = _load("fiche_decorateur_452.html")  # fiche complète (tél + site)

# Fragment structurel (mêmes classes label.title_details / div.infos_details que
# les fiches réelles ci-dessus) simulant une fiche "Pays" hors France -- aucun
# exemple de ce cas n'a été capturé en sonde (623 fiches, échantillon limité) ;
# la garde hors-cible reste couverte déterministiquement sur cette base réelle.
FICHE_PAYS_ETRANGER = """
<header><h1>Déco Bruxelles Intérieurs</h1></header>
<div class="column_in">
<div class="form_details"><label class="title_details">Adresse</label>
<div class="infos_details">Rue Neuve 12</div></div>
<div class="form_details"><label class="title_details">Code postal</label>
<div class="infos_details">1000</div></div>
<div class="form_details"><label class="title_details">Ville</label>
<div class="infos_details">bruxelles</div></div>
<div class="form_details"><label class="title_details">Pays</label>
<div class="infos_details">belgique</div></div>
<div class="form_details"><label class="title_details">Téléphone</label>
<div class="infos_details">022345678</div></div>
</div>
"""


def test_parse_list_page_extracts_rows_and_drops_cross_category():
    # Page réelle : ~20 cartes dont 1 injectée par le moteur Arfooo appartenant
    # à la catégorie "coach-decoration" (observée au run réel) -> écartée.
    rows = parse_list_page(LIST_DECORATEUR_P1, "decorateur-d-interieur")
    assert all(r["fiche_url"].startswith(
        "https://annuairedecoration.fr/decorateur-d-interieur/") for r in rows)
    # La fiche cross-catégorie connue (Sésame intérieur, coach-decoration) est
    # absente malgré sa présence dans le HTML brut de la page.
    assert not any("sesame-interieur" in r["fiche_url"] for r in rows)
    assert any("agence-nathalie-mothe" in r["fiche_url"] for r in rows)
    r = next(r for r in rows if "agence-nathalie-mothe" in r["fiche_url"])
    assert r["fiche_id"] == "452"
    assert r["title"] == "Agence Nathalie Mothe - Décoration d'Intérieur - Paris"
    assert r["website"] == "www.nathaliemothe.com"


def test_parse_list_page_architecte_category():
    rows = parse_list_page(LIST_ARCHITECTE_P1, "architecte-d-interieur")
    assert len(rows) >= 15  # ~20 cartes/page
    fiche_ids = {r["fiche_id"] for r in rows}
    assert "429" in fiche_ids and "12" in fiche_ids


def test_parse_max_page():
    assert parse_max_page(LIST_ARCHITECTE_P1) == 5
    assert parse_max_page(LIST_DECORATEUR_P1) == 5
    assert parse_max_page("<div>pas de pagination</div>") == 1


def test_parse_fiche_complete_target():
    f = parse_fiche(FICHE_ARCHITECTE_429, "429")
    assert f is not None
    assert f["title"] == "Architecte d'intérieur, décorateur d'intérieur Paris - E-interiorconcept"
    assert f["cp"] == "91420"
    assert f["city"] == "morangis"
    assert f["phone"] == "09 77 21 58 28"
    assert f["website"] == "http://www.e-interiorconcept.com/"


def test_parse_fiche_decorateur_complete_target():
    f = parse_fiche(FICHE_DECORATEUR_452, "452")
    assert f is not None
    assert f["cp"] == "75015" and f["city"] == "paris"
    assert f["phone"] == "06 62 13 12 59"
    assert f["website"] == "http://www.nathaliemothe.com/#!contact-nathalie-mothe/c15n8"


def test_parse_fiche_without_contact_block_stays_valid():
    # Régression réelle (fiche 12) : pas de section "Informations sur la
    # société" du tout (aucun form_details société) -> pas de crash, champs
    # vides plutôt qu'inventés (VIDE > FAUX), fiche gardée (titre présent).
    f = parse_fiche(FICHE_ARCHITECTE_12, "12")
    assert f is not None
    assert f["title"] == "Violaine ROUCH - architecte DPLG"
    assert f["phone"] is None
    assert f["city"] == "" and f["cp"] == ""


def test_parse_fiche_pays_etranger_is_dropped():
    # Garde hors-cible : répertoire généraliste, sites étrangers écartés.
    assert parse_fiche(FICHE_PAYS_ETRANGER, "9001") is None


def test_parse_fiche_missing_h1_returns_none():
    assert parse_fiche("<div>page cassée</div>", "0") is None


@pytest.mark.parametrize("raw,expected", [
    ("0977215828", "09 77 21 58 28"),
    ("0662131259", "06 62 13 12 59"),
    ("01 53 68 91 80", "01 53 68 91 80"),
    ("+33 6 62 13 12 59", "06 62 13 12 59"),
    ("", None),
    (None, None),
    ("123", None),          # trop court -> VIDE > FAUX, pas de numéro inventé
    ("12345678901234", None),  # trop long / pas un numéro FR
])
def test_normalize_phone_fr(raw, expected):
    assert normalize_phone_fr(raw) == expected


def test_connector_fetch_paginates_and_drops_hors_cible():
    pages = {
        "https://annuairedecoration.fr/architecte-d-interieur/": LIST_ARCHITECTE_P1,
        "https://annuairedecoration.fr/architecte-d-interieur/"
        "architecte-d-interieur-decorateur-d-interieur-paris-e-interiorconcept-s429.html":
            FICHE_ARCHITECTE_429,
        "https://annuairedecoration.fr/architecte-d-interieur/"
        "violaine-rouch-architecte-dplg-s12.html": FICHE_ARCHITECTE_12,
    }
    calls = []

    def fake(url):
        calls.append(url)
        return pages.get(url)

    conn = AnnuaireDecorationConnector(
        http_fetch=fake, categories={"architecte-d-interieur": "architecte d'intérieur"})
    records = conn.fetch(limit=100, max_pages=1)
    fiche_ids = {r["fiche_id"] for r in records}
    assert {"429", "12"} <= fiche_ids
    assert conn.last_total_count >= 15
    # Throttle : jamais deux fois la même URL fetchée.
    assert len(calls) == len(set(calls))


def test_connector_fetch_bounded_by_limit():
    conn = AnnuaireDecorationConnector(
        http_fetch=lambda u: LIST_ARCHITECTE_P1 if u.endswith("/architecte-d-interieur/")
        else FICHE_ARCHITECTE_429,
        categories={"architecte-d-interieur": "architecte d'intérieur"})
    records = conn.fetch(limit=3, max_pages=1)
    assert len(records) <= 3


def test_to_candidates_maps_architecte_annuaire():
    conn = AnnuaireDecorationConnector(http_fetch=lambda u: None)
    cand = conn.to_candidates([{
        "fiche_id": "429",
        "title": "Architecte d'intérieur, décorateur d'intérieur Paris - E-interiorconcept",
        "address": "19 avenue maurice barres", "cp": "91420", "city": "morangis",
        "phone": "09 77 21 58 28", "website": "http://www.e-interiorconcept.com/",
        "fiche_url": "https://annuairedecoration.fr/architecte-d-interieur/x-s429.html",
        "category_label": "architecte d'intérieur",
    }])[0]
    assert cand.source == "annuaire" and cand.source_ref == "annuairedecoration:429"
    assert cand.population == "architecte"
    assert cand.lifecycle_label == "studio_actif"
    assert cand.main_signal == "prescripteur actif"
    assert cand.establishment_type == "architecte d'intérieur"
    assert cand.city == "morangis"
    assert "91420" in cand.address and "morangis" in cand.address
    assert cand.website == "http://www.e-interiorconcept.com/"
    assert "annuaire annuairedecoration" in cand.secondary_signals
    # Régression : le téléphone doit être reporté dans raw['phone'] -- seul
    # chemin lu par pipeline._process_candidate pour remplir Opportunity.phone
    # (même contrat que CFAI/UFDI/Places).
    assert cand.raw.get("phone") == "09 77 21 58 28"


def test_to_candidates_decorateur_category_label():
    conn = AnnuaireDecorationConnector(http_fetch=lambda u: None)
    cand = conn.to_candidates([{
        "fiche_id": "452", "title": "Agence Nathalie Mothe", "address": "",
        "cp": "75015", "city": "paris", "phone": None, "website": None,
        "fiche_url": "x", "category_label": "décorateur d'intérieur",
    }])[0]
    assert cand.establishment_type == "décorateur d'intérieur"
    assert not cand.raw.get("phone")  # VIDE > FAUX : jamais de chaîne vide falsy-piégeuse


def test_to_candidates_without_phone_leaves_raw_falsy():
    conn = AnnuaireDecorationConnector(http_fetch=lambda u: None)
    cand = conn.to_candidates([{
        "fiche_id": "1", "title": "X", "address": "", "cp": "", "city": "",
        "phone": None, "website": None, "fiche_url": "x",
        "category_label": "architecte d'intérieur",
    }])[0]
    assert not cand.raw.get("phone")
    assert cand.email is None
