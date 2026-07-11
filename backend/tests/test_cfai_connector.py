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

# Extrait RÉEL de l'adhérent 81 (run réel 2026-07-11) : le marqueur honoraire est
# dans .member-company, PAS dans .member-activity-summary → doit aussi être écarté.
FICHE_HONORAIRE_COMPANY = """
<header><h1>Dominik BOUVIER</h1>
<p class="member-company">Retraité - Membre Honoraire</p></header>
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


def test_parse_fiche_honoraire_in_company_is_dropped():
    # Régression run réel (adhérent 81) : marqueur honoraire dans .member-company.
    assert parse_fiche(FICHE_HONORAIRE_COMPANY, "81") is None


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
