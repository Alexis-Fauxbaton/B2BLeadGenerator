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
