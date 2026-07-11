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
