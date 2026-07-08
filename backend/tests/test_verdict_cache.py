# backend/tests/test_verdict_cache.py
"""Tests du cache de verdicts (brique 3) — horloge injectée, session en mémoire."""
from datetime import date

from sqlmodel import Session, SQLModel, create_engine

from app.models import HandleVerdict
from app.ingestion import verdict_cache as vc


def _session():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


PROF = {"biography": "bio de test", "postsCount": 5}


def test_profile_hash_depends_on_bio_and_posts():
    h1 = vc.profile_hash({"biography": "a", "postsCount": 3})
    assert vc.profile_hash({"biography": "a", "postsCount": 3}) == h1  # stable
    assert vc.profile_hash({"biography": "a", "postsCount": 9}) != h1  # postsCount
    assert vc.profile_hash({"biography": "b", "postsCount": 3}) != h1  # bio


def test_revisit_after_windows():
    t = date(2026, 7, 6)
    assert vc.revisit_after("not_venue", t) == date(2027, 7, 6)          # +12 mois
    assert vc.revisit_after("established", t) == date(2027, 1, 6)        # +6 mois
    assert vc.revisit_after("chain_multisite", t) == date(2027, 1, 6)   # +6 mois
    assert vc.revisit_after("noise", t) == date(2026, 9, 6)             # +2 mois
    assert vc.revisit_after("unknown", t) == date(2026, 9, 6)          # +2 mois
    assert vc.revisit_after("opening_soon", t) is None                  # watchlist
    assert vc.revisit_after("just_opened", t) is None


def test_should_rejudge_no_entry_is_true():
    with _session() as s:
        assert vc.should_rejudge(s, "inconnu") is True


def test_should_rejudge_respects_window():
    with _session() as s:
        vc.upsert(s, "x", "not_venue", "haute", PROF, today=date(2026, 7, 6))
        # Dans la fenêtre (12 mois) -> pas de re-jugement.
        assert vc.should_rejudge(s, "x", today=date(2026, 12, 1)) is False
        # Fenêtre expirée -> re-jugement.
        assert vc.should_rejudge(s, "x", today=date(2027, 8, 1)) is True


def test_should_rejudge_on_profile_hash_change():
    with _session() as s:
        vc.upsert(s, "x", "established", "haute", PROF, today=date(2026, 7, 6))
        # Profil identique, dans la fenêtre -> False.
        assert vc.should_rejudge(s, "x", PROF, today=date(2026, 8, 1)) is False
        # Bio changée -> re-juger même dans la fenêtre.
        changed = {"biography": "NOUVELLE bio", "postsCount": 5}
        assert vc.should_rejudge(s, "x", changed, today=date(2026, 8, 1)) is True


def test_opening_never_cached_always_rejudged():
    with _session() as s:
        v = vc.upsert(s, "o", "opening_soon", "haute", PROF, today=date(2026, 7, 6))
        assert v.revisit_after is None
        # Le jour même : toujours re-jugé (watchlist active).
        assert vc.should_rejudge(s, "o", today=date(2026, 7, 6)) is True


def test_upsert_updates_in_place():
    with _session() as s:
        vc.upsert(s, "x", "noise", "basse", PROF, today=date(2026, 7, 6))
        vc.upsert(s, "x", "established", "haute", PROF, today=date(2026, 7, 6))
        rows = s.query(HandleVerdict).all() if hasattr(s, "query") else None
        v = vc.get(s, "x")
        assert v.verdict == "established" and v.confidence == "haute"
        # Un seul enregistrement pour ce handle (upsert, pas d'insert dupliqué).
        from sqlmodel import select
        assert len(s.exec(select(HandleVerdict).where(HandleVerdict.handle == "x")).all()) == 1
