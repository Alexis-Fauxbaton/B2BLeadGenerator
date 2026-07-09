# backend/tests/test_lifecycle_label.py
"""Tests de la colonne lifecycle_label (brique 3bis, T1) : migration, persistance,
exposition API + filtre. Aucun réseau."""
from datetime import date

from sqlmodel import Session, SQLModel, create_engine, select

from app.ingestion.base import LeadCandidate
from app.ingestion.pipeline import IngestStats, _process_candidate
from app.models import Opportunity
from app.routes.opportunities import list_opportunities


def _engine():
    e = create_engine("sqlite://")
    SQLModel.metadata.create_all(e)
    return e


def test_signal_types_contains_neutral():
    from app.models import SIGNAL_TYPES
    assert "établissement en activité" in SIGNAL_TYPES


def test_leadcandidate_has_lifecycle_label():
    c = LeadCandidate(source="instagram", source_ref="x", establishment_name="X",
                      city="Paris", main_signal="ouverture prochaine",
                      detection_date=date(2026, 7, 6), establishment_type="restaurant",
                      lifecycle_label="opening_soon")
    assert c.lifecycle_label == "opening_soon"


def test_process_candidate_persists_lifecycle_label():
    with Session(_engine()) as s:
        cand = LeadCandidate(
            source="instagram", source_ref="etabli1", establishment_name="Vieux Bistrot",
            city="Paris", address="", main_signal="établissement en activité",
            detection_date=date(2026, 7, 6), establishment_type="restaurant",
            lifecycle_label="established",
        )
        _process_candidate(s, cand, IngestStats(source="instagram"), set(), enricher=None)
        s.commit()
        opp = s.exec(select(Opportunity).where(Opportunity.source_ref == "etabli1")).first()
        assert opp is not None and opp.lifecycle_label == "established"


def test_process_candidate_update_refreshes_lifecycle_label():
    with Session(_engine()) as s:
        base = dict(source="instagram", source_ref="h1", establishment_name="H",
                    city="Paris", address="", detection_date=date(2026, 7, 6),
                    establishment_type="restaurant")
        _process_candidate(s, LeadCandidate(main_signal="établissement en activité",
                                            lifecycle_label="unknown", **base),
                           IngestStats(source="instagram"), set(), enricher=None)
        s.commit()
        _process_candidate(s, LeadCandidate(main_signal="ouverture prochaine",
                                            lifecycle_label="opening_soon", **base),
                           IngestStats(source="instagram"), set(), enricher=None)
        s.commit()
        opp = s.exec(select(Opportunity).where(Opportunity.source_ref == "h1")).first()
        assert opp.lifecycle_label == "opening_soon"  # rafraîchi à l'upsert même-source


def test_api_filters_by_lifecycle_label():
    with Session(_engine()) as s:
        for ref, lab, sig in [("a", "established", "établissement en activité"),
                              ("b", "opening_soon", "ouverture prochaine")]:
            _process_candidate(
                s, LeadCandidate(source="instagram", source_ref=ref, establishment_name=ref,
                                 city="Paris", address="", main_signal=sig,
                                 detection_date=date(2026, 7, 6), establishment_type="restaurant",
                                 lifecycle_label=lab),
                IngestStats(source="instagram"), set(), enricher=None)
        s.commit()
        got = list_opportunities(session=s, lifecycle_label="established")
        assert [o.source_ref for o in got] == ["a"]


def test_migration_adds_column_on_existing_db(tmp_path):
    from sqlalchemy import create_engine as ce, inspect, text
    import app.database as db
    url = f"sqlite:///{tmp_path/'legacy.db'}"
    # Base « ancienne » sans la colonne.
    old = ce(url)
    with old.begin() as conn:
        conn.execute(text("CREATE TABLE opportunities (id INTEGER PRIMARY KEY, "
                          "establishment_name VARCHAR, establishment_type VARCHAR, "
                          "city VARCHAR, address VARCHAR, main_signal VARCHAR, "
                          "detection_date DATE, estimated_timing VARCHAR)"))
    old.dispose()
    # Repointer le moteur du module vers cette base, puis migrer.
    orig_engine, orig_url = db.engine, db.DATABASE_URL
    db.engine, db.DATABASE_URL = ce(url), url
    try:
        db._run_lightweight_migrations()
        cols = {c["name"] for c in inspect(db.engine).get_columns("opportunities")}
        assert "lifecycle_label" in cols
    finally:
        db.engine.dispose()
        db.engine, db.DATABASE_URL = orig_engine, orig_url
