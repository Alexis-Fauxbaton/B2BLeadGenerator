"""Tests du script de backfill one-shot `app.ingestion.backfill_followers`.
Aucun réseau : la source Apify est monkeypatchée."""
from datetime import date

from sqlmodel import Session, SQLModel, create_engine, select

from app.ingestion import backfill_followers as bf
from app.models import Opportunity


def _engine():
    e = create_engine("sqlite://")
    SQLModel.metadata.create_all(e)
    return e


def _opp(**kw):
    base = dict(
        establishment_name="X", establishment_type="architecte d'intérieur",
        city="Paris", address="", main_signal="prescripteur actif",
        detection_date=date(2026, 7, 10), estimated_timing="J-90",
        source="instagram", population="architecte",
    )
    base.update(kw)
    return Opportunity(**base)


def test_backfill_finds_from_disk_snapshot(monkeypatch):
    # imaginonschezvous.json existe dans snapshots_architectes avec followersCount=582.
    monkeypatch.setattr(bf.instagram_mod, "scrape_profiles", lambda handles, **k: {})
    with Session(_engine()) as s:
        s.add(_opp(source_ref="imaginonschezvous", instagram="imaginonschezvous"))
        s.commit()
        stats = bf.run(s)
        assert stats["from_snapshot"] == 1
        assert stats["from_apify"] == 0
        opp = s.exec(select(Opportunity).where(Opportunity.source_ref == "imaginonschezvous")).first()
        assert opp.followers_count == 582


def test_backfill_falls_back_to_apify_when_no_snapshot(monkeypatch):
    monkeypatch.setattr(bf.instagram_mod, "scrape_profiles",
                        lambda handles, **k: {"handle_sans_snapshot": {"followersCount": 1234}})
    with Session(_engine()) as s:
        s.add(_opp(source_ref="handle_sans_snapshot", instagram="handle_sans_snapshot"))
        s.commit()
        stats = bf.run(s)
        assert stats["from_snapshot"] == 0
        assert stats["from_apify"] == 1
        opp = s.exec(select(Opportunity).where(Opportunity.source_ref == "handle_sans_snapshot")).first()
        assert opp.followers_count == 1234


def test_backfill_leaves_still_missing_when_apify_returns_nothing(monkeypatch):
    """Fail-soft (pas de token / handle introuvable) : VIDE > FAUX -> reste NULL."""
    monkeypatch.setattr(bf.instagram_mod, "scrape_profiles", lambda handles, **k: {})
    with Session(_engine()) as s:
        s.add(_opp(source_ref="handle_introuvable", instagram="handle_introuvable"))
        s.commit()
        stats = bf.run(s)
        assert stats["still_missing"] == 1
        opp = s.exec(select(Opportunity).where(Opportunity.source_ref == "handle_introuvable")).first()
        assert opp.followers_count is None


def test_backfill_skips_rows_already_populated(monkeypatch):
    monkeypatch.setattr(bf.instagram_mod, "scrape_profiles", lambda handles, **k: {})
    with Session(_engine()) as s:
        s.add(_opp(source_ref="deja_rempli", instagram="deja_rempli", followers_count=99))
        s.commit()
        stats = bf.run(s)
        assert stats["candidates"] == 0
        opp = s.exec(select(Opportunity).where(Opportunity.source_ref == "deja_rempli")).first()
        assert opp.followers_count == 99


def test_backfill_dry_run_does_not_write(monkeypatch):
    monkeypatch.setattr(bf.instagram_mod, "scrape_profiles", lambda handles, **k: {})
    with Session(_engine()) as s:
        s.add(_opp(source_ref="imaginonschezvous", instagram="imaginonschezvous"))
        s.commit()
        stats = bf.run(s, dry_run=True)
        assert stats["from_snapshot"] == 1
        s.expire_all()
        opp = s.exec(select(Opportunity).where(Opportunity.source_ref == "imaginonschezvous")).first()
        assert opp.followers_count is None
