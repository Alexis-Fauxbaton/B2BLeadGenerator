"""Tests de la récupération de téléphones (chantier phones architectes).

Couvre SANS RÉSEAU : normalisation FR, extraction tel:/regex d'une page HTML,
désambiguïsation par palier (multi-numéros -> vide), garde « site propre »,
mapping de confiance, sélection DB et waterfall (site -> Places) via doublures.
Doctrine VIDE > FAUX : un numéro ambigu ou d'un mauvais site -> aucun numéro."""
from datetime import date

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.ingestion import enrich_phones as ep
from app.ingestion.enrich_phones import (
    _confidence_for,
    _enrich_one_phone,
    _own_site,
    _phone_targets,
    PhoneStats,
)
from app.ingestion.enrichment.contact_enricher import ContactInfo
from app.ingestion.enrichment.website_scraper import (
    choose_phone,
    extract_phones_from_html,
    normalize_fr_phone,
)
from app.models import Opportunity


def _engine():
    e = create_engine("sqlite://")
    SQLModel.metadata.create_all(e)
    return e


# --- Normalisation FR -----------------------------------------------------------


@pytest.mark.parametrize("raw,expected", [
    ("0123456789", "01 23 45 67 89"),
    ("01 23 45 67 89", "01 23 45 67 89"),
    ("01.23.45.67.89", "01 23 45 67 89"),
    ("01-23-45-67-89", "01 23 45 67 89"),
    ("+33 1 23 45 67 89", "01 23 45 67 89"),
    ("+33123456789", "01 23 45 67 89"),
    ("0033 1 23 45 67 89", "01 23 45 67 89"),
    ("06 12 34 56 78", "06 12 34 56 78"),
    ("(01) 23 45 67 89", "01 23 45 67 89"),
])
def test_normalize_valid(raw, expected):
    assert normalize_fr_phone(raw) == expected


@pytest.mark.parametrize("raw", [
    None, "", "12345", "0023456789",          # second chiffre 0 -> invalide
    "123456789012", "abcdefghij", "00 00 00 00 00",
])
def test_normalize_invalid_returns_none(raw):
    assert normalize_fr_phone(raw) is None


def test_normalize_is_dedup_key():
    # Deux écritures du même numéro -> même forme normalisée (clé de dédup).
    assert normalize_fr_phone("+33123456789") == normalize_fr_phone("01.23.45.67.89")


# --- Extraction d'une page HTML -------------------------------------------------


def test_extract_tel_and_text_separated():
    html = '<a href="tel:+33123456789">appelez</a> ou au 06 11 22 33 44 depuis le texte'
    out = extract_phones_from_html(html)
    assert out["tel"] == ["01 23 45 67 89"]
    # Le numéro du lien tel: est AUSSI capté par la regex texte (inoffensif : le
    # palier tel: décide en premier dans choose_phone), + le numéro en clair.
    assert out["text"] == ["01 23 45 67 89", "06 11 22 33 44"]


def test_extract_dedup_same_number():
    html = '<a href="tel:0123456789">x</a> 01 23 45 67 89 01.23.45.67.89'
    out = extract_phones_from_html(html)
    assert out["tel"] == ["01 23 45 67 89"]
    # le même numéro, écrit trois fois, n'apparaît qu'une fois après dédup
    assert out["text"] == ["01 23 45 67 89"]


def test_extract_ignores_css_keyframe_noise():
    # Bug réel trouvé en contrôle anti-faux : une valeur de timing CSS
    # `0.00009999999999999999%` dans un <style> matchait FR_PHONE_RE et
    # produisait le faux numéro "09 99 99 99 99" (jamais affiché sur le site).
    html = (
        "<style>@keyframes glideIn {"
        "0.00009999999999999999% { opacity: 1; }"
        "}</style>"
        "<p>Aucun numero affiche ici.</p>"
    )
    out = extract_phones_from_html(html)
    assert out["tel"] == []
    assert out["text"] == []


def test_extract_ignores_script_block_noise():
    html = (
        "<script>var config = {version: '0912345678'};</script>"
        "<a href=\"tel:0611223344\">appelez</a>"
    )
    out = extract_phones_from_html(html)
    assert out["tel"] == ["06 11 22 33 44"]
    assert out["text"] == ["06 11 22 33 44"]


# --- Désambiguïsation par palier (choose_phone) ---------------------------------


def test_choose_single_tel_wins():
    pages = [{"is_contact": False, "tel": ["01 23 45 67 89"], "text": ["09 88 77 66 55"]}]
    # tel: (palier 1) prime sur un numéro texte, même s'il diffère.
    assert choose_phone(pages) == "01 23 45 67 89"


def test_choose_multiple_distinct_tel_is_ambiguous_empty():
    pages = [{"is_contact": True, "tel": ["01 23 45 67 89", "06 11 22 33 44"], "text": []}]
    # Deux tel: distincts -> ambigu -> vide (on ne redescend pas d'un palier).
    assert choose_phone(pages) is None


def test_choose_contact_page_over_home():
    pages = [
        {"is_contact": False, "tel": [], "text": ["09 00 00 00 01"]},   # home
        {"is_contact": True, "tel": [], "text": ["01 23 45 67 89"]},    # contact
    ]
    assert choose_phone(pages) == "01 23 45 67 89"


def test_choose_multiple_on_contact_is_ambiguous_empty():
    pages = [{"is_contact": True, "tel": [], "text": ["01 23 45 67 89", "01 99 88 77 66"]}]
    assert choose_phone(pages) is None


def test_choose_falls_back_to_home_when_only_home_has_number():
    pages = [{"is_contact": False, "tel": [], "text": ["01 23 45 67 89"]}]
    assert choose_phone(pages) == "01 23 45 67 89"


def test_choose_same_tel_repeated_across_pages_not_ambiguous():
    pages = [
        {"is_contact": False, "tel": ["01 23 45 67 89"], "text": []},
        {"is_contact": True, "tel": ["01 23 45 67 89"], "text": []},
    ]
    assert choose_phone(pages) == "01 23 45 67 89"


def test_choose_nothing_returns_none():
    assert choose_phone([{"is_contact": False, "tel": [], "text": []}]) is None


# --- Garde « site propre » ------------------------------------------------------


@pytest.mark.parametrize("url", [
    "https://mokko-agencement.fr/", "http://claiarchitecture.com/",
])
def test_own_site_accepts_real_domain(url):
    assert _own_site(url) == url.strip()


@pytest.mark.parametrize("url", [
    None, "", "http://www.tiktok.com/@_designproject_",
    "http://linkedin.com/in/helene-gombert", "https://www.houzz.fr/pro/x/__public",
    "https://instagram.com/foo", "https://linktr.ee/foo", "https://facebook.com/foo",
])
def test_own_site_rejects_social_and_portal(url):
    assert _own_site(url) is None


# --- Mapping de confiance -------------------------------------------------------


@pytest.mark.parametrize("basis,expected", [
    ("site", "haute"), ("apify", "haute"), ("geo", "haute"),
    ("text", "basse"), (None, "basse"),
])
def test_confidence_for(basis, expected):
    assert _confidence_for(basis) == expected


# --- Sélection DB ---------------------------------------------------------------


def _mk_opp(**kw):
    base = dict(
        establishment_name="Studio X", establishment_type="architecte d'intérieur",
        city="Bordeaux", address="", main_signal="prescripteur actif",
        detection_date=date(2026, 7, 10), estimated_timing="J-90",
        source="instagram", population="architecte",
    )
    base.update(kw)
    return Opportunity(**base)


def test_phone_targets_only_population_without_phone():
    with Session(_engine()) as s:
        s.add(_mk_opp(source_ref="a", phone=None))                     # cible
        s.add(_mk_opp(source_ref="b", phone="01 23 45 67 89"))         # a déjà un tél
        s.add(_mk_opp(source_ref="c", population="chr", phone=None))   # autre population
        s.commit()
        rows = _phone_targets(s, "architecte", 500)
        refs = {o.source_ref for o in rows}
        assert refs == {"a"}


# --- Waterfall (doublures, sans réseau) -----------------------------------------


class _FakeEnricher:
    def __init__(self, info):
        self._info = info

    def enrich(self, *a, **k):
        return self._info


class _FakeSirene:
    def lookup(self, siren):
        return None


def test_enrich_one_uses_site_first_high_confidence(monkeypatch):
    monkeypatch.setattr(ep, "scrape_phone", lambda url: "01 23 45 67 89")
    opp = _mk_opp(website="https://claiarchitecture.com/")
    stats = PhoneStats()
    _enrich_one_phone(opp, _FakeEnricher(ContactInfo(phone="09 99 99 99 99")),
                      _FakeSirene(), stats)
    # Le site (palier 1) prime sur Places -> haute, et Places n'est pas consulté.
    assert opp.phone == "01 23 45 67 89"
    assert opp.contact_confidence == "haute"
    assert opp.contact_enriched_at is not None
    assert (stats.with_phone, stats.high_conf) == (1, 1)


def test_enrich_one_falls_back_to_places_when_site_empty(monkeypatch):
    monkeypatch.setattr(ep, "scrape_phone", lambda url: None)
    opp = _mk_opp(website="https://claiarchitecture.com/")
    stats = PhoneStats()
    info = ContactInfo(phone="01.23.45.67.89", match_basis="text")  # nom fort, non géo
    _enrich_one_phone(opp, _FakeEnricher(info), _FakeSirene(), stats)
    assert opp.phone == "01 23 45 67 89"          # normalisé
    assert opp.contact_confidence == "basse"      # text -> basse
    assert (stats.with_phone, stats.low_conf) == (1, 1)


def test_enrich_one_geo_match_is_high_confidence(monkeypatch):
    monkeypatch.setattr(ep, "scrape_phone", lambda url: None)
    opp = _mk_opp(website=None)
    stats = PhoneStats()
    info = ContactInfo(phone="01 23 45 67 89", match_basis="geo")
    _enrich_one_phone(opp, _FakeEnricher(info), _FakeSirene(), stats)
    assert opp.contact_confidence == "haute"


def test_enrich_one_no_phone_marks_tried(monkeypatch):
    monkeypatch.setattr(ep, "scrape_phone", lambda url: None)
    opp = _mk_opp(website="https://claiarchitecture.com/")
    stats = PhoneStats()
    _enrich_one_phone(opp, _FakeEnricher(ContactInfo(phone=None)), _FakeSirene(), stats)
    assert opp.phone is None
    assert opp.contact_enriched_at is not None    # a tenté (ne sera pas re-scanné si on filtre)
    assert stats.none == 1


def test_enrich_one_never_overwrites_existing_phone(monkeypatch):
    monkeypatch.setattr(ep, "scrape_phone", lambda url: "01 23 45 67 89")
    opp = _mk_opp(website="https://claiarchitecture.com/", phone="06 00 00 00 00")
    stats = PhoneStats()
    _enrich_one_phone(opp, _FakeEnricher(ContactInfo(phone=None)), _FakeSirene(), stats)
    assert opp.phone == "06 00 00 00 00"          # VIDE > FAUX : jamais d'écrasement
    assert stats.with_phone == 0


def test_enrich_one_skips_scrape_for_social_website(monkeypatch):
    called = {"n": 0}

    def _spy(url):
        called["n"] += 1
        return "01 23 45 67 89"

    monkeypatch.setattr(ep, "scrape_phone", _spy)
    opp = _mk_opp(website="http://www.tiktok.com/@x")
    _enrich_one_phone(opp, _FakeEnricher(ContactInfo(phone=None)), _FakeSirene(), PhoneStats())
    assert called["n"] == 0                        # on ne scrape pas un profil social
    assert opp.phone is None
