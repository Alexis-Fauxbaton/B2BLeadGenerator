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
    contact_page_urls,
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


def test_extract_drops_template_junk_phones():
    # Numéros de démo de template (observés sur plusieurs sites sans lien) :
    # jamais celui du lead, écartés dès l'extraction.
    html = '<p>08 51 15 89 55</p><a href="tel:0612345678">x</a>'
    out = extract_phones_from_html(html)
    assert out["tel"] == ["06 12 34 56 78"]
    assert out["text"] == ["06 12 34 56 78"]


def test_cross_domain_junk_flags_shared_numbers():
    from app.ingestion.enrich_phones import cross_domain_junk
    results = [
        (1, "06 11 22 33 44", "a-studio.fr"),
        (2, "08 99 88 77 66", "b-studio.fr"),
        (3, "08 99 88 77 66", "c-studio.fr"),   # même numéro, autre domaine -> junk
        (4, "06 11 22 33 44", "a-studio.fr"),   # même domaine (doublon) -> OK
        (5, "07 00 11 22 33", None),            # domaine illisible -> ignoré
    ]
    assert cross_domain_junk(results) == {"08 99 88 77 66"}


def test_home_url_variants_covers_scheme_and_www():
    from app.ingestion.enrichment.website_scraper import home_url_variants
    # Cas réel parallel.fr : l'URL stockée (http+www) est morte, https sans www vit.
    variants = home_url_variants("http://www.parallel.fr")
    assert variants[0] == "http://www.parallel.fr"
    assert "https://parallel.fr" in variants
    assert "https://www.parallel.fr" in variants
    assert len(variants) == len(set(variants))


def test_enrich_one_phone_defers_site_phone_when_pending_list_given(monkeypatch):
    import app.ingestion.enrich_phones as ep
    monkeypatch.setattr(ep, "scrape_phone", lambda url: "06 55 44 33 22")
    monkeypatch.setattr(ep, "_own_site", lambda w: w)
    opp = Opportunity(establishment_name="X", establishment_type="architecte",
                      city="Paris", website="https://x-studio.fr")
    stats = PhoneStats()
    pending = []
    ep._enrich_one_phone(opp, _FakeEnricher(ContactInfo(phone=None)), _FakeSirene(), stats, pending)
    # différé : rien d'écrit sur la fiche, numéro en attente de la garde
    assert opp.phone is None
    assert pending == [(opp, "06 55 44 33 22")]
    assert stats.with_phone == 0 and stats.none == 0
    assert opp.contact_enriched_at is not None


def test_extract_international_zero_convention():
    # Bug réel (parallel.fr) : numéro affiché « +33 (0)1.47.42.14.38 » — la
    # convention (0) n'était ni matchée par la regex ni normalisée sans double 0.
    html = "<p>Tél. +33 (0)1.47.42.14.38</p>"
    out = extract_phones_from_html(html)
    assert out["text"] == ["01 47 42 14 38"]
    html2 = "<p>+33(0) 1 47 42 14 38</p>"
    assert extract_phones_from_html(html2)["text"] == ["01 47 42 14 38"]


def test_extract_ignores_digit_runs_like_timestamps():
    # Bug réel (parallel.fr) : le timestamp `4v1520348869305` d'une iframe
    # Google Maps contenait 10 chiffres plausibles -> faux « 03 48 86 93 05 ».
    # Les gardes de frontière rejettent tout match collé à d'autres chiffres.
    html = '<iframe src="https://www.google.com/maps/embed?pb=!1m18!2sus!4v1520348869305"></iframe>'
    out = extract_phones_from_html(html)
    assert out["tel"] == []
    assert out["text"] == []


def test_choose_home_text_unique_corroborated_by_contact_wins():
    # Cas deniseomerdesign.fr : la home affiche UN numéro (texte), la page
    # contact le répète avec un second (fixe + mobile du même studio). Le
    # numéro mis en avant dès l'accueil ET corroboré en contact prime.
    pages = [
        {"is_contact": False, "tel": [], "text": ["06 15 94 64 36"]},
        {"is_contact": True, "is_legal": False, "tel": [],
         "text": ["06 15 94 64 36", "01 40 06 99 12"]},
    ]
    assert choose_phone(pages) == "06 15 94 64 36"


def test_choose_home_text_without_contact_corroboration_does_not_fire():
    # Home et contact affichent des numéros DIFFÉRENTS : pas de corroboration
    # -> le numéro unique de la vraie page contact décide (palier 2).
    pages = [
        {"is_contact": False, "tel": [], "text": ["06 15 94 64 36"]},
        {"is_contact": True, "is_legal": False, "tel": [], "text": ["01 40 06 99 12"]},
    ]
    assert choose_phone(pages) == "01 40 06 99 12"


def test_choose_contact_page_number_beats_legal_page_noise():
    # Cas raphaelgilardino.com : la page contact affiche LE numéro du studio,
    # les mentions légales ajoutent celui de l'opérateur (Monaco Telecom) —
    # le numéro unique de la page contact stricte doit primer.
    pages = [
        {"is_contact": False, "tel": [], "text": []},
        {"is_contact": True, "is_legal": False, "tel": [], "text": ["+377 92 05 23 21"]},
        {"is_contact": True, "is_legal": True, "tel": [],
         "text": ["+377 92 05 23 21", "+377 97 70 30 90"]},
    ]
    assert choose_phone(pages) == "+377 92 05 23 21"


def test_extract_monaco_number_from_text():
    # Bug réel (AGENCE CRAI / raphaelgilardino.com) : studio CFAI basé à Monaco,
    # numéro affiché en texte libre « Tél : +377 92 05 23 21 » (le lien a un
    # href="#") — le pipeline France-only le voyait mais le jetait.
    html = '<p>T&eacute;l : +377 92 05 23 21</p><a href="#" class="tel">+377 92 05 23 21</a>'
    out = extract_phones_from_html(html)
    assert out["tel"] == []
    assert out["text"] == ["+377 92 05 23 21"]


def test_normalize_mc_phone_variants():
    from app.ingestion.enrichment.website_scraper import normalize_mc_phone, normalize_phone
    assert normalize_mc_phone("+377 92 05 23 21") == "+377 92 05 23 21"
    assert normalize_mc_phone("00377 92.05.23.21") == "+377 92 05 23 21"
    assert normalize_mc_phone("+377 92 05 23") is None  # trop court
    assert normalize_mc_phone("0612345678") is None  # FR n'est pas du ressort MC
    # normalize_phone : FR prioritaire, Monaco en repli, même clé de dédup
    assert normalize_phone("+33123456789") == "01 23 45 67 89"
    assert normalize_phone("+37792052321") == "+377 92 05 23 21"


def test_extract_ignores_unterminated_script_tail():
    # Bug réel (zephyrinbonal.com) : page Wix tronquée au cap au MILIEU d'un
    # <script> — sans fermant, le bloc n'était pas retiré et son JSON produisait
    # des faux numéros ("09 99 99 99 99"...). Le bloc orphelin doit être coupé.
    html = (
        '<a href="tel:0612345678">appel</a>'
        '<script>{"junk": "09 99 99 99 99 et 01 59 99 99 99'
    )
    out = extract_phones_from_html(html)
    assert out["tel"] == ["06 12 34 56 78"]
    assert out["text"] == ["06 12 34 56 78"]


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


def test_choose_home_tel_wins_over_secondary_contact_tel():
    # Cas réel m2-scene : la home ne déclare qu'UN tel: (le mobile canonique) ;
    # les pages contact/mentions ajoutent un fixe secondaire. Le tel: UNIQUE de
    # la home décide et ne se laisse plus diluer en « multi-numéros -> vide ».
    pages = [
        {"is_contact": False, "tel": ["06 09 32 24 28"],
         "text": ["06 86 50 80 01", "06 09 32 24 28"]},
        {"is_contact": True, "tel": ["01 86 26 03 39", "06 09 32 24 28"],
         "text": ["06 00 00 00 00", "01 86 26 03 39", "06 09 32 24 28"]},
        {"is_contact": True, "tel": ["01 86 26 03 39", "06 09 32 24 28"],
         "text": ["01 86 26 03 39", "06 09 32 24 28"]},
    ]
    assert choose_phone(pages) == "06 09 32 24 28"


def test_choose_ambiguous_home_tel_stays_empty():
    # Deux tel: distincts SUR LA HOME = source autoritaire ambiguë -> vide
    # (on ne redescend pas piocher un numéro d'une page moins sûre).
    pages = [
        {"is_contact": False, "tel": ["01 23 45 67 89", "06 11 22 33 44"], "text": []},
        {"is_contact": True, "tel": ["01 23 45 67 89"], "text": []},
    ]
    assert choose_phone(pages) is None


def test_choose_single_text_on_contact_retained_when_no_tel():
    # Numéro FR UNIQUE en texte sur la page contact du site propre (aucun tel:)
    # = fiable -> retenu (doctrine : ambiguïté multi-numéros -> vide inchangée).
    pages = [
        {"is_contact": False, "tel": [], "text": []},
        {"is_contact": True, "tel": [], "text": ["05 56 12 34 56"]},
    ]
    assert choose_phone(pages) == "05 56 12 34 56"


# --- Découverte robuste de la page contact (contact_page_urls) -------------------


def test_contact_urls_discovers_relative_link():
    html = '<nav><a href="/contact">Contact</a><a href="/projets">Projets</a></nav>'
    urls = contact_page_urls(html, "https://exemple.fr")
    assert "https://exemple.fr/contact" in urls
    assert all("projets" not in u for u in urls)


def test_contact_urls_discovers_absolute_and_ignores_other_domains():
    html = (
        '<a href="https://exemple.fr/nous-contacter/">Contact</a>'
        '<a href="https://facebook.com/exemple/contact">FB</a>'
    )
    urls = contact_page_urls(html, "https://exemple.fr")
    assert "https://exemple.fr/nous-contacter/" in urls
    assert all("facebook.com" not in u for u in urls)


def test_contact_urls_tolerates_www_and_scheme_variations():
    # Home servie en www ; lien contact relatif -> résolu sur le même hôte.
    html = '<a href="mentions-legales/">Mentions légales</a>'
    urls = contact_page_urls(html, "https://www.exemple.fr")
    assert "https://www.exemple.fr/mentions-legales/" in urls


def test_contact_urls_excludes_home_and_dedups_trailing_slash():
    # Un lien qui pointe vers la home elle-même ne doit pas être re-sondé ;
    # /contact et /contact/ sont la même page (slash final normalisé).
    html = '<a href="/">Accueil</a><a href="/contact/">Contact</a><a href="/contact">Contact</a>'
    urls = contact_page_urls(html, "https://exemple.fr")
    # La home ("/") n'est jamais re-sondée.
    assert "https://exemple.fr" not in [u.rstrip("/") for u in urls]
    # /contact et /contact/ = même page (slash final normalisé) -> une seule entrée.
    contact = [u for u in urls if u.rstrip("/").endswith("/contact")]
    assert len({u.rstrip("/") for u in contact}) == 1


def test_contact_urls_falls_back_to_static_paths_when_no_links():
    urls = contact_page_urls("<p>rien</p>", "https://exemple.fr")
    assert "https://exemple.fr/contact" in urls
    assert "https://exemple.fr/mentions-legales" in urls


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


def test_phone_targets_sites_only_filters_websiteless():
    # Leçon du 2026-07-14 : sans ce filtre, les ~3 000 fiches stock sans site
    # consomment la limite et les fiches à site ne sont jamais scannées.
    with Session(_engine()) as s:
        s.add(_mk_opp(source_ref="a", phone=None, website="https://a-studio.fr"))
        s.add(_mk_opp(source_ref="b", phone=None))                # pas de site
        s.add(_mk_opp(source_ref="c", phone=None, website=""))    # site vide
        s.commit()
        refs = {o.source_ref for o in _phone_targets(s, "architecte", 500, sites_only=True)}
        assert refs == {"a"}
        refs_all = {o.source_ref for o in _phone_targets(s, "architecte", 500)}
        assert refs_all == {"a", "b", "c"}


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
