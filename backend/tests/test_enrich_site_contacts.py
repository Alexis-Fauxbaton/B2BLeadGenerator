"""Tests de la passe email/Instagram/Facebook depuis le site du lead (chantier
enrich_site_contacts, branche enfin ``website_scraper.scrape_contacts``).

Couvre SANS RÉSEAU : extraction/désambiguïsation multi-comptes Instagram,
scrape du site (requests.get monkeypatché), sélection DB, et l'enrichisseur
(remplissage des vides uniquement, non-écrasement, confiance non dégradée).
Doctrine VIDE > FAUX : email placeholder ou Instagram ambigu -> aucun contact
plutôt qu'un contact faux."""
from datetime import date

import pytest
from sqlmodel import Session, SQLModel, create_engine

import app.ingestion.enrich_site_contacts as esc
import app.ingestion.enrichment.website_scraper as ws
from app.ingestion.enrich_site_contacts import (
    SiteContactStats,
    _enrich_one_site_contact,
    _site_contact_targets,
)
from app.ingestion.enrichment.website_scraper import (
    choose_instagram,
    extract_instagram_handles,
    scrape_site_contacts,
)
from app.models import Opportunity


def _engine():
    e = create_engine("sqlite://")
    SQLModel.metadata.create_all(e)
    return e


# --- Extraction Instagram d'une page ---------------------------------------------


def test_extract_instagram_handles_dedupes_same_handle():
    html = (
        '<a href="https://instagram.com/juliebarbeau">insta</a>'
        '<a href="https://www.instagram.com/juliebarbeau/">encore</a>'
    )
    assert extract_instagram_handles(html) == ["juliebarbeau"]


def test_extract_instagram_handles_filters_ignored_paths():
    html = '<a href="https://instagram.com/p/abc123">post</a>'
    assert extract_instagram_handles(html) == []


def test_extract_instagram_handles_multiple_distinct():
    html = (
        '<a href="https://instagram.com/juliebarbeau">a</a>'
        '<a href="https://instagram.com/autrecompte">b</a>'
    )
    assert extract_instagram_handles(html) == ["juliebarbeau", "autrecompte"]


# --- Désambiguïsation multi-pages (choose_instagram) ------------------------------


def test_choose_instagram_single_handle_wins():
    pages = [["juliebarbeau"], ["juliebarbeau"]]
    assert choose_instagram(pages) == "juliebarbeau"


def test_choose_instagram_case_insensitive_same_handle():
    pages = [["JulieBarbeau"], ["juliebarbeau"]]
    assert choose_instagram(pages) == "JulieBarbeau"


def test_choose_instagram_multiple_distinct_is_ambiguous_empty():
    pages = [["juliebarbeau"], ["autrecompte"]]
    assert choose_instagram(pages) is None


def test_choose_instagram_nothing_returns_none():
    assert choose_instagram([[], []]) is None


# --- Scrape du site (requests.get monkeypatché) -----------------------------------


class _FakeResp:
    def __init__(self, text, status_code=200):
        self.status_code = status_code
        self.headers = {"content-type": "text/html"}
        self.text = text


def _fake_get(pages_by_url):
    def get(url, headers=None, timeout=None):
        if url in pages_by_url:
            return _FakeResp(pages_by_url[url])
        return _FakeResp("", status_code=404)
    return get


def test_scrape_site_contacts_fills_email_insta_facebook(monkeypatch):
    home = (
        '<a href="mailto:contact@juliebarbeaudecoration.fr">email</a>'
        '<a href="https://instagram.com/juliebarbeaudeco">insta</a>'
        '<a href="https://facebook.com/juliebarbeaudeco">fb</a>'
    )
    monkeypatch.setattr(
        ws.requests, "get",
        _fake_get({"https://juliebarbeaudecoration.fr": home}),
    )
    out = scrape_site_contacts("https://juliebarbeaudecoration.fr")
    assert out["email"] == "contact@juliebarbeaudecoration.fr"
    assert out["instagram"] == "juliebarbeaudeco"
    assert out["facebook"] == "juliebarbeaudeco"


def test_scrape_site_contacts_multi_instagram_across_pages_is_empty(monkeypatch):
    home = '<a href="https://instagram.com/handlea">insta</a>'
    contact = '<a href="https://instagram.com/handleb">insta</a>'
    monkeypatch.setattr(
        ws.requests, "get",
        _fake_get({
            "https://example.fr": home,
            "https://example.fr/contact": contact,
        }),
    )
    out = scrape_site_contacts("https://example.fr")
    assert out["instagram"] is None    # ambigu -> vide


def test_scrape_site_contacts_placeholder_email_is_empty(monkeypatch):
    home = '<input type="email" placeholder="sophie@email.com">'
    monkeypatch.setattr(
        ws.requests, "get",
        _fake_get({"https://example.fr": home}),
    )
    out = scrape_site_contacts("https://example.fr")
    assert out["email"] is None        # placeholder de formulaire -> filtré


def test_scrape_site_contacts_no_phone_key_leaked():
    # scrape_site_contacts ne renvoie jamais de téléphone (rôle d'enrich_phones).
    assert set(scrape_site_contacts("")) == {"email", "instagram", "facebook"}


# --- Sélection DB ------------------------------------------------------------------


def _mk_opp(**kw):
    base = dict(
        establishment_name="Studio X", establishment_type="architecte d'intérieur",
        city="Bordeaux", address="", main_signal="prescripteur actif",
        detection_date=date(2026, 7, 10), estimated_timing="J-90",
        source="instagram", population="architecte",
    )
    base.update(kw)
    return Opportunity(**base)


def test_site_contact_targets_needs_own_site_and_missing_field():
    with Session(_engine()) as s:
        s.add(_mk_opp(source_ref="a", website="https://a.fr", email=None,
                       instagram=None, facebook=None))                        # cible
        s.add(_mk_opp(source_ref="b", website="https://b.fr", email="e@b.fr",
                       instagram="b", facebook="b"))                          # déjà complet
        s.add(_mk_opp(source_ref="c", website="https://instagram.com/c",
                       email=None))                                           # pas un site propre
        s.add(_mk_opp(source_ref="d", website=None, email=None))              # pas de site
        s.add(_mk_opp(source_ref="e", website="https://e.fr", email=None,
                       population="chr"))                                     # autre population
        s.commit()
        rows = _site_contact_targets(s, "architecte", 500)
        refs = {o.source_ref for o in rows}
        assert refs == {"a"}


# --- Enrichisseur (doublures, sans réseau) -----------------------------------------


def test_enrich_one_fills_only_empty_fields(monkeypatch):
    monkeypatch.setattr(esc, "scrape_site_contacts", lambda url: {
        "email": "contact@juliebarbeaudecoration.fr",
        "instagram": "juliebarbeaudeco",
        "facebook": "juliebarbeaudeco",
    })
    opp = _mk_opp(website="https://juliebarbeaudecoration.fr")
    stats = SiteContactStats()
    _enrich_one_site_contact(opp, stats)
    assert opp.email == "contact@juliebarbeaudecoration.fr"
    assert opp.instagram == "juliebarbeaudeco"
    assert opp.facebook == "juliebarbeaudeco"
    assert opp.contact_confidence == "haute"           # posée (pas de confiance avant)
    assert opp.contact_enriched_at is not None
    assert (stats.email_filled, stats.insta_filled, stats.fb_filled) == (1, 1, 1)


def test_enrich_one_never_overwrites_existing_fields(monkeypatch):
    monkeypatch.setattr(esc, "scrape_site_contacts", lambda url: {
        "email": "autre@site.fr",
        "instagram": "autrehandle",
        "facebook": "autrefb",
    })
    opp = _mk_opp(
        website="https://juliebarbeaudecoration.fr",
        email="deja@present.fr", instagram="deja_present", facebook="deja_present",
    )
    stats = SiteContactStats()
    _enrich_one_site_contact(opp, stats)
    assert opp.email == "deja@present.fr"
    assert opp.instagram == "deja_present"
    assert opp.facebook == "deja_present"
    assert (stats.email_filled, stats.insta_filled, stats.fb_filled) == (0, 0, 0)
    assert stats.none == 1


def test_enrich_one_fills_only_the_missing_field(monkeypatch):
    monkeypatch.setattr(esc, "scrape_site_contacts", lambda url: {
        "email": "contact@site.fr", "instagram": "handle", "facebook": "handlefb",
    })
    opp = _mk_opp(
        website="https://site.fr",
        email=None, instagram="deja_present", facebook="deja_present",
    )
    stats = SiteContactStats()
    _enrich_one_site_contact(opp, stats)
    assert opp.email == "contact@site.fr"       # vide -> rempli
    assert opp.instagram == "deja_present"      # déjà rempli -> inchangé
    assert opp.facebook == "deja_present"       # déjà rempli -> inchangé
    assert (stats.email_filled, stats.insta_filled, stats.fb_filled) == (1, 0, 0)


def test_enrich_one_multi_instagram_stays_empty(monkeypatch):
    # scrape_site_contacts a déjà tranché (ambigu -> None) ; l'enrichisseur ne
    # doit rien inventer derrière.
    monkeypatch.setattr(esc, "scrape_site_contacts", lambda url: {
        "email": None, "instagram": None, "facebook": None,
    })
    opp = _mk_opp(website="https://site.fr")
    stats = SiteContactStats()
    _enrich_one_site_contact(opp, stats)
    assert opp.instagram is None
    assert opp.contact_confidence is None       # rien trouvé -> pas de confiance posée
    assert stats.none == 1


def test_enrich_one_never_degrades_existing_confidence(monkeypatch):
    monkeypatch.setattr(esc, "scrape_site_contacts", lambda url: {
        "email": "contact@site.fr", "instagram": None, "facebook": None,
    })
    opp = _mk_opp(website="https://site.fr", email=None, contact_confidence="basse")
    stats = SiteContactStats()
    _enrich_one_site_contact(opp, stats)
    assert opp.email == "contact@site.fr"
    assert opp.contact_confidence == "basse"    # jamais dégradée ni ré-écrasée en "haute"


def test_enrich_one_skips_scrape_for_non_own_site(monkeypatch):
    called = {"n": 0}

    def _spy(url):
        called["n"] += 1
        return {"email": "x@x.fr", "instagram": "x", "facebook": "x"}

    monkeypatch.setattr(esc, "scrape_site_contacts", _spy)
    opp = _mk_opp(website="https://instagram.com/foo")
    stats = SiteContactStats()
    _enrich_one_site_contact(opp, stats)
    assert called["n"] == 0
    assert opp.email is None
    assert stats.none == 1


def test_enrich_one_marks_tried_even_when_empty(monkeypatch):
    monkeypatch.setattr(esc, "scrape_site_contacts", lambda url: {
        "email": None, "instagram": None, "facebook": None,
    })
    opp = _mk_opp(website="https://site.fr")
    stats = SiteContactStats()
    _enrich_one_site_contact(opp, stats)
    assert opp.contact_enriched_at is not None
    assert stats.none == 1
