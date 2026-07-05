"""Tests du matcher Insta -> SIREN/SIRET (cas réels des snapshots d'éval)."""
from app.ingestion.enrichment.siret_matcher import (
    clean_name,
    street_number,
    _name_overlap,
)


def test_clean_name_strips_emojis_and_decorations():
    assert clean_name("MOKA ☕️ Coffee shop & Matcha Bar 🍵") == "MOKA Coffee shop & Matcha Bar"
    # 𝐺𝑖𝑜𝑟𝑔𝑖𝑛𝑎 en "mathematical alphanumeric symbols" -> NFKC -> Giorgina
    assert clean_name("\U0001d43a\U0001d456\U0001d45c\U0001d45f\U0001d454\U0001d456\U0001d45b\U0001d44e 💙") == "Giorgina"


def test_clean_name_keeps_first_segment_before_separators():
    assert clean_name("LE MOURE ROUGE - CANNES 🛟") == "LE MOURE ROUGE"
    assert clean_name("VILLA HENRIETTE • CABOURG") == "VILLA HENRIETTE"
    assert clean_name("Brasserie de la Fontaine • Lourmarin") == "Brasserie de la Fontaine"
    assert clean_name("l'Artémise-Salon de thé") == "l'Artémise"


def test_clean_name_handles_empty():
    assert clean_name(None) == ""
    assert clean_name("🍕🍕") == ""


def test_street_number():
    assert street_number("143  Av. du Général de Gaule Sartrouville") == "143"
    assert street_number("11 rue du Colisée, 75008, Paris") == "11"
    assert street_number("Place de la Fontaine, Lourmarin") is None
    assert street_number(None) is None


def test_name_overlap_uses_distinctive_tokens():
    # 'restaurant'/'le'/'la' sont génériques : pas de match dessus.
    assert _name_overlap("Tre Gusto", "SAR FOOD") is False
    assert _name_overlap("LE MOURE ROUGE", "LE MOURE ROUGE 56.10A CANNES") is True
    assert _name_overlap("LE MOURE ROUGE", "COMMUNE DE CANNES MAIRIE") is False
    assert _name_overlap("CHÈRES COUSINES", "CC ROQUETTE (CHERES COUSINES)") is True


from app.ingestion.enrichment.siret_matcher import _candidates, pick_by_name

# Extraits réels de l'API recherche-entreprises (test du 2026-07-04).
HIT_MOURE = {
    "siren": "899355770", "nom_complet": "LE MOURE ROUGE",
    "activite_principale": "56.10A", "date_creation": "2021-05-17",
    "siege": {"siret": "89935577000012", "activite_principale": "56.10A",
              "adresse": "62 BOULEVARD DE LA CROISETTE 06400 CANNES",
              "code_postal": "06400", "liste_enseignes": None},
}
HIT_MAIRIE = {
    "siren": "210600292", "nom_complet": "COMMUNE DE CANNES",
    "activite_principale": "84.11Z", "date_creation": "1901-01-01",
    "siege": {"siret": "21060029200010", "activite_principale": "84.11Z",
              "adresse": "PL DE L HOTEL DE VILLE 06150 CANNES",
              "code_postal": "06150", "liste_enseignes": ["MAIRIE"]},
}
HIT_AUREA = {
    "siren": "105726145", "nom_complet": "AUREA",
    "activite_principale": "56.10A", "date_creation": "2026-05-28",
    "siege": {"siret": "10572614500014", "activite_principale": "56.10A",
              "adresse": "8 RUE DU LANGUEDOC 06590 THEOULE-SUR-MER",
              "code_postal": "06590", "liste_enseignes": None},
}
# Variante near_point : l'établissement matché est dans matching_etablissements.
HIT_OCOIN = {
    "siren": "989119201", "nom_complet": "OCOIN",
    "date_creation": "2025-01-15",
    "matching_etablissements": [{
        "siret": "98911920100011", "activite_principale": "56.10C",
        "adresse": "143 AVENUE GENERAL DE GAULLE 78500 SARTROUVILLE",
        "code_postal": "78500", "liste_enseignes": None,
        "date_creation": "2025-07-04",
    }],
}


def test_candidates_normalizes_siege_and_matching_etablissements():
    cands = _candidates([HIT_MOURE, HIT_OCOIN])
    assert cands[0]["siren"] == "899355770"
    assert cands[0]["naf"] == "56.10A"
    assert cands[0]["adresse"] == "62 BOULEVARD DE LA CROISETTE 06400 CANNES"
    # near_point : l'étage établissement prime sur le siège.
    assert cands[1]["siret"] == "98911920100011"
    assert cands[1]["naf"] == "56.10C"


def test_pick_by_name_accepts_with_geo_consistency():
    cands = _candidates([HIT_MAIRIE, HIT_MOURE])
    got = pick_by_name(cands, "LE MOURE ROUGE", city="Cannes", postal=None)
    # La mairie (NAF non-CHR, pas d'overlap distinctif) est ignorée.
    assert got is not None and got["siren"] == "899355770"


def test_pick_by_name_refuses_without_geo():
    # Piège Auréa : nom+NAF collent mais aucune géo connue -> PAS d'auto-accept
    # (ira à l'arbitre). Le backfill actuel aurait mergé à tort.
    cands = _candidates([HIT_AUREA])
    assert pick_by_name(cands, "AURÉA", city=None, postal=None) is None


def test_pick_by_name_refuses_geo_mismatch():
    cands = _candidates([HIT_AUREA])
    assert pick_by_name(cands, "AURÉA", city="Lisbonne", postal=None) is None


def test_http_get_fails_soft(monkeypatch):
    import app.ingestion.enrichment.siret_matcher as sm

    def boom(*a, **k):
        raise OSError("réseau HS")

    monkeypatch.setattr(sm.requests, "get", boom)
    assert sm._http_get(sm.SEARCH_URL, {"q": "x"}) == {}
