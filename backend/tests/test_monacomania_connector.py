# backend/tests/test_monacomania_connector.py
"""Connecteur MonacoMania — Architectes de Monaco (A2) — parsing PUR sur
extraits RÉELS de la page (tests/fixtures/monacomania/architectes-de-monaco.html,
récupérée poliment le 2026-07-17, throttle >= 2,5 s ; une seule page, non
paginée -- sonde). Aucun réseau : http_fetch injecté."""
from pathlib import Path

import pytest

from app.ingestion.annuaires.monacomania import (
    MonacomaniaConnector,
    normalize_phone_mc,
    parse_card,
    parse_list,
    slugify,
)

FIXTURES = Path(__file__).parent / "fixtures" / "monacomania"
FULL_PAGE = (FIXTURES / "architectes-de-monaco.html").read_text(encoding="utf-8")

# Extraits RÉELS de architectes-de-monaco.html (une carte = une <table width="800">).

# Cas simple : un seul <span class="style5"> englobant desc+adresse+tél+lien.
CARD_RAYMOND = """
<table width="800" border="0" align="center" cellpadding="0" cellspacing="0">
  <tr>
    <td width="191" align="left" valign="bottom"><div align="left"><a href="http://raymond-architecte.mc/" target="_blank"><img src="architectes-de-monaco/architectes-de-monaco01.jpg" width="180" height="130" border="0" align="left" title="ATELIER RAYMOND ARCHITECTES"></a></div></td>
    <td width="729" align="left" valign="middle"><div align="center">
      <p><span class="style133"><strong>ATELIER RAYMOND ARCHITECTES  </strong></span></p>
      <p> <br>
        <span class="style5">Atelier d'architecture de Monaco <br>
        5, rue Louis Notari
<br>
MC-98000 MONACO<br>
Tel: +377 97 70 75 37</span><br>
            <a href="http://raymond-architecte.mc/" target="_blank">+ info »</a></p>
      </div></td>
  </tr>
</table>
"""

# Cas « description hors span » : texte libre AVANT le <span class="style5">
# (« Architecture » hors span, reste dedans) -- structure la plus irrégulière
# observée sur les 6 cabinets réels, et seule fiche mentionnant explicitement
# « intérieur » dans son descriptif libre.
CARD_FRED_GENIN = """
<table width="800" border="0" align="center" cellpadding="0" cellspacing="0">
  <tr>
    <td width="191" align="left" valign="bottom"><div align="left"><a href="http://www.archmonaco.net/" target="_blank"><img src="architectes-de-monaco/architectes-de-monaco03.jpg" width="180" height="130" border="0" align="left" title="ARCH - FRED GENIN"></a></div></td>
    <td width="729" align="left" valign="middle"><div align="center">
      <p><span class="style133"><strong>ARCH - FRED GENIN  </strong></span> </p>
      <p>&nbsp;</p>
      <p>Architecture <span class="style5">| architécture d'intérieur | design | concours <br>
        8, rue Suffren Reymond <br>
        MC-98000 MONACO<br>
        Tel: +377 92 05 94 44 <br>
        <a href="http://www.archmonaco.net/" target="_blank">+ info »</a></span></p>
    </div></td>
  </tr>
</table>
"""

# Cas « deux spans distincts » : description dans un 1er span.style5 séparé de
# l'adresse/tél/lien (2e span.style5).
CARD_ATELIER_VII = """
<table width="800" border="0" align="center" cellpadding="0" cellspacing="0">
  <tr>
    <td width="191" align="left" valign="bottom"><div align="left"><a href="http://atelier7monaco.com/" target="_blank"><img src="architectes-de-monaco/architectes-de-monaco05.jpg" width="180" height="130" border="0" align="left" title="ATELIER VII ARCHITECTURE"></a></div></td>
    <td width="729" align="left" valign="middle"><div align="center">
        <p><span class="style133"><strong>ATELIER VII  ARCHITECTURE</strong></span> </p>
        <p>&nbsp;</p>
      <p><span class="style5">Atelier d'architecture de Monaco</span><span class="style5"> <br>
        «Tour Odéon» 36, av. de l'Annonciade <br>
        MC-98000 MONACO<br>
        Tel: +377 97 70 06 93 <br>
        <a href="http://atelier7monaco.com/" target="_blank">+ info »</a></span></p>
    </div></td>
  </tr>
</table>
"""

# Bloc publicitaire (« Ajouter votre site ») -- nom dans span.style9 + <a>, PAS
# span.style133 strong -- doit être ignoré structurellement par parse_list.
CARD_AD_BLOCK = """
<table width="800" border="0" align="center" cellpadding="0" cellspacing="0">
  <tr>
    <td width="186" align="left" valign="bottom"><div align="left"><a href="https://www.monacomania.com/ajouter-votre-site-a-monacomania/" target="_top"><img src="ajouter-votre-site-a-monacomania/ajouter-votre-site-a-monacomania-click.jpg" width="180" height="145" border="0" align="left" title="AJOUTER VOTRE SITE À MONACOMANIA.COM"></a></div></td>
    <td width="614" align="left" valign="middle"><div align="center">
        <p><span class="style9"><a href="https://www.monacomania.com/ajouter-votre-site-a-monacomania/" target="_top">AJOUTER VOTRE SITE À MONACOMANIA.COM</a> </span> <br>
            <br>
          <a href="https://www.monacomania.com/ajouter-votre-site-a-monacomania/" target="_top" class="style5">+ info »</a></p>
    </div></td>
  </tr>
</table>
"""

# Ordre des Architectes de Monaco -- institution, même structure de carte que
# les cabinets (span.style133 strong) mais NI adresse NI téléphone.
CARD_ORDRE = """
<table width="800" border="0" align="center" cellpadding="0" cellspacing="0">
  <tr>
    <td width="191" align="left" valign="bottom"><div align="left"><a href="http://www.architectes-monaco.com/" target="_blank"><img src="architectes-de-monaco/ordre-des-architectes-de-monaco.jpg" width="180" height="130" border="0" align="left" title="ORDRE DES ARCHITECTES DE MONACO"></a></div></td>
    <td width="729" align="left" valign="middle"><div align="center">
        <p><span class="style133"><strong>ORDRE DES ARCHITECTES DE MONACO </strong></span></p>
        <p><span class="style5"><br>
            <a href="http://www.architectes-monaco.com/" target="_blank">+ info »</a></span></p>
      </div></td>
  </tr>
</table>
"""


def test_parse_card_simple_single_span():
    c = parse_card(CARD_RAYMOND)
    assert c is not None
    assert c["name"] == "ATELIER RAYMOND ARCHITECTES"
    assert c["slug"] == "atelier-raymond-architectes"
    assert c["address"] == "5, rue Louis Notari"
    assert c["city"] == "Monaco"
    assert c["phone"] == "+377 97 70 75 37"
    assert c["website"] == "http://raymond-architecte.mc/"
    assert c["description"] == "Atelier d'architecture de Monaco"


def test_parse_card_description_outside_span():
    c = parse_card(CARD_FRED_GENIN)
    assert c is not None
    assert c["name"] == "ARCH - FRED GENIN"
    assert c["address"] == "8, rue Suffren Reymond"
    assert c["phone"] == "+377 92 05 94 44"
    assert c["website"] == "http://www.archmonaco.net/"
    assert c["description"] == "Architecture | architécture d'intérieur | design | concours"


def test_parse_card_two_separate_spans():
    c = parse_card(CARD_ATELIER_VII)
    assert c is not None
    assert c["name"] == "ATELIER VII  ARCHITECTURE"
    assert c["address"] == "«Tour Odéon» 36, av. de l'Annonciade"
    assert c["phone"] == "+377 97 70 06 93"
    assert c["website"] == "http://atelier7monaco.com/"
    assert c["description"] == "Atelier d'architecture de Monaco"


def test_parse_card_ad_block_returns_none():
    # Pas de span.style133 -> pas une carte cabinet.
    assert parse_card(CARD_AD_BLOCK) is None


def test_parse_card_institution_returns_none():
    # Garde hors-cible : ni tél ni adresse -> institution, pas un cabinet.
    assert parse_card(CARD_ORDRE) is None


def test_parse_card_missing_style133_returns_none():
    assert parse_card("<div>page cassée</div>") is None


@pytest.mark.parametrize("raw,expected", [
    ("Tel: +377 97 70 75 37", "+377 97 70 75 37"),
    ("Tel: +377 93 25 17 65 ", "+377 93 25 17 65"),
    ("+377 92 05 94 44", "+377 92 05 94 44"),
    ("0033612345678", None),   # pas un numéro monégasque (indicatif FR)
    ("377970123", None),       # 9 chiffres seulement (pas 8 après 377)
    ("", None),
    (None, None),
])
def test_normalize_phone_mc(raw, expected):
    assert normalize_phone_mc(raw) == expected


@pytest.mark.parametrize("name,expected", [
    ("ATELIER RAYMOND ARCHITECTES", "atelier-raymond-architectes"),
    ("ATELIER NATACHA MORIN-INNOCENTI (NMI)", "atelier-natacha-morin-innocenti-nmi"),
    ("ARCH - FRED GENIN", "arch-fred-genin"),
    ("", "sans-nom"),
])
def test_slugify(name, expected):
    assert slugify(name) == expected


def test_parse_list_on_full_real_page():
    # Page réelle complète : 6 cabinets + Ordre (écarté) + bloc pub (écarté).
    rows = parse_list(FULL_PAGE)
    names = {r["name"] for r in rows}
    assert names == {
        "ATELIER RAYMOND ARCHITECTES",
        "ATELIER NATACHA MORIN-INNOCENTI (NMI)",
        "ARCH - FRED GENIN",
        "RAINIER BOISSON ARCHITECTES",
        "ATELIER VII  ARCHITECTURE",
        "ARCHI STUDIO",
    }
    assert "ORDRE DES ARCHITECTES DE MONACO" not in names
    assert "AJOUTER VOTRE SITE À MONACOMANIA.COM" not in names
    # Rendement : 100 % des cabinets retenus ont un téléphone exploitable.
    assert all(r["phone"] for r in rows)


def test_connector_fetch_single_page_no_pagination():
    calls = []

    def fake(url):
        calls.append(url)
        return FULL_PAGE

    conn = MonacomaniaConnector(http_fetch=fake)
    records = conn.fetch(limit=100)
    assert len(records) == 6
    assert conn.last_total_count == 6
    # Une seule requête HTTP (page non paginée -- pas de fiches détaillées).
    assert calls == ["https://www.monacomania.com/architectes-de-monaco.php"]


def test_connector_fetch_bounded_by_limit():
    conn = MonacomaniaConnector(http_fetch=lambda u: FULL_PAGE)
    records = conn.fetch(limit=2)
    assert len(records) == 2
    # last_total_count reflète le total réel (6), pas la troncature par limit.
    assert conn.last_total_count == 6


def test_connector_fetch_returns_empty_when_http_fetch_fails():
    conn = MonacomaniaConnector(http_fetch=lambda u: None)
    records = conn.fetch(limit=100)
    assert records == []
    assert conn.last_total_count == 0


def test_to_candidates_maps_architecte_annuaire():
    conn = MonacomaniaConnector(http_fetch=lambda u: None)
    cand = conn.to_candidates([{
        "slug": "atelier-raymond-architectes", "name": "ATELIER RAYMOND ARCHITECTES",
        "description": "Atelier d'architecture de Monaco",
        "address": "5, rue Louis Notari", "city": "Monaco",
        "phone": "+377 97 70 75 37", "website": "http://raymond-architecte.mc/",
    }])[0]
    assert cand.source == "annuaire"
    assert cand.source_ref == "monacomania:atelier-raymond-architectes"
    assert cand.population == "architecte"
    assert cand.lifecycle_label == "studio_actif"
    assert cand.main_signal == "prescripteur actif"
    assert cand.establishment_name == "ATELIER RAYMOND ARCHITECTES"
    assert cand.city == "Monaco"
    assert cand.address == "5, rue Louis Notari"
    assert cand.website == "http://raymond-architecte.mc/"
    assert cand.establishment_type == "architecte"  # pas de mention "intérieur"
    assert "annuaire monacomania" in cand.secondary_signals
    assert cand.email is None
    assert cand.decision_maker is None
    # Régression : le téléphone doit être reporté dans raw['phone'] -- seul
    # chemin lu par pipeline._process_candidate pour remplir Opportunity.phone
    # (même contrat que CFAI/UFDI/Annuaire Décoration/Mon Architecte d'Intérieur/Places).
    assert cand.raw.get("phone") == "+377 97 70 75 37"


def test_to_candidates_detects_interieur_mention():
    conn = MonacomaniaConnector(http_fetch=lambda u: None)
    cand = conn.to_candidates([{
        "slug": "arch-fred-genin", "name": "ARCH - FRED GENIN",
        "description": "Architecture | architécture d'intérieur | design | concours",
        "address": "8, rue Suffren Reymond", "city": "Monaco",
        "phone": "+377 92 05 94 44", "website": "http://www.archmonaco.net/",
    }])[0]
    assert cand.establishment_type == "architecte d'intérieur"
    assert "mention architecture d'intérieur" in cand.secondary_signals


def test_to_candidates_without_phone_leaves_raw_falsy():
    conn = MonacomaniaConnector(http_fetch=lambda u: None)
    cand = conn.to_candidates([{
        "slug": "x", "name": "X", "description": "", "address": "12 rue X",
        "city": "Monaco", "phone": None, "website": None,
    }])[0]
    assert not cand.raw.get("phone")
    assert cand.email is None
