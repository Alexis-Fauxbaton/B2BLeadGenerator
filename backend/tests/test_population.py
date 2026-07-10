"""Tests de la colonne population (A1, T1) : migration, persistance, contournement
du classifieur CHR pour les architectes, exposition API + filtre. Aucun réseau."""
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


def test_signal_types_contains_prescriber_labels():
    from app.models import SIGNAL_TYPES
    for s in ("prescripteur actif", "projet CHR détecté",
              "portfolio hospitality/CHR", "studio en sommeil"):
        assert s in SIGNAL_TYPES


def test_leadcandidate_defaults_to_chr():
    c = LeadCandidate(source="bodacc", source_ref="x", establishment_name="X",
                      city="Paris", main_signal="ouverture prochaine",
                      detection_date=date(2026, 7, 10))
    assert c.population == "chr"


def test_process_candidate_persists_population_architecte():
    with Session(_engine()) as s:
        cand = LeadCandidate(
            source="instagram", source_ref="studio1", establishment_name="Studio X",
            city="Bordeaux", address="", main_signal="prescripteur actif",
            detection_date=date(2026, 7, 10), establishment_type="architecte d'intérieur",
            lifecycle_label="studio_actif", population="architecte",
        )
        _process_candidate(s, cand, IngestStats(source="instagram"), set(), enricher=None)
        s.commit()
        opp = s.exec(select(Opportunity).where(Opportunity.source_ref == "studio1")).first()
        assert opp is not None
        assert opp.population == "architecte"
        assert opp.establishment_type == "architecte d'intérieur"


def test_architecte_bypasses_chr_classifier_even_with_non_chr_naf():
    # NAF archi (71.11Z) : le classifieur CHR renverrait None et dropperait le lead.
    # La branche population-aware doit le GARDER (type pris tel quel).
    with Session(_engine()) as s:
        cand = LeadCandidate(
            source="instagram", source_ref="studio2", establishment_name="Atelier Y",
            city="Lyon", address="", main_signal="prescripteur actif",
            detection_date=date(2026, 7, 10), establishment_type="architecte d'intérieur",
            naf="71.11Z", population="architecte",
        )
        _process_candidate(s, cand, IngestStats(source="instagram"), set(), enricher=None)
        s.commit()
        opp = s.exec(select(Opportunity).where(Opportunity.source_ref == "studio2")).first()
        assert opp is not None and opp.population == "architecte"


def test_chr_lead_still_dropped_by_non_chr_naf():
    # Non-régression : un lead CHR (population par défaut) avec un NAF non-CHR
    # reste DROPPÉ (le contournement ne s'applique QU'aux architectes).
    with Session(_engine()) as s:
        cand = LeadCandidate(
            source="bodacc", source_ref="holding1", establishment_name="Holding Immo",
            city="Paris", address="", main_signal="ouverture prochaine",
            detection_date=date(2026, 7, 10), naf="68.20A",  # immobilier, non-CHR
            classification_text="hôtel restaurant SCI",
        )
        _process_candidate(s, cand, IngestStats(source="bodacc"), set(), enricher=None)
        s.commit()
        assert s.exec(select(Opportunity).where(Opportunity.source_ref == "holding1")).first() is None


def test_api_filters_by_population():
    with Session(_engine()) as s:
        for ref, pop, sig, etype in [
            ("chr1", "chr", "ouverture prochaine", "restaurant"),
            ("arc1", "architecte", "prescripteur actif", "architecte d'intérieur"),
        ]:
            _process_candidate(
                s, LeadCandidate(source="instagram", source_ref=ref, establishment_name=ref,
                                 city="Paris", address="", main_signal=sig,
                                 detection_date=date(2026, 7, 10), establishment_type=etype,
                                 population=pop),
                IngestStats(source="instagram"), set(), enricher=None)
        s.commit()
        got = list_opportunities(session=s, population="architecte")
        assert [o.source_ref for o in got] == ["arc1"]


def test_dashboard_stats_default_excludes_architectes():
    # Le dashboard CHR ne doit PAS être pollué par les leads architectes : par
    # défaut get_stats filtre population=='chr' (compteurs, by_signal, hottest).
    from app.routes.dashboard import get_stats
    with Session(_engine()) as s:
        for ref, pop, sig, etype in [
            ("chrA", "chr", "ouverture prochaine", "restaurant"),
            ("chrB", "chr", "ouverture prochaine", "bar"),
            ("arcA", "architecte", "prescripteur actif", "architecte d'intérieur"),
        ]:
            _process_candidate(
                s, LeadCandidate(source="instagram", source_ref=ref, establishment_name=ref,
                                 city="Paris", address="", main_signal=sig,
                                 detection_date=date(2026, 7, 10), establishment_type=etype,
                                 population=pop),
                IngestStats(source="instagram"), set(), enricher=None)
        s.commit()
        default = get_stats(session=s)  # défaut 'chr'
        assert default.total_opportunities == 2
        assert all(b.label != "prescripteur actif" for b in default.by_signal)
        assert all(o.population == "chr" for o in default.hottest)
        assert get_stats(session=s, population="architecte").total_opportunities == 1
        assert get_stats(session=s, population="").total_opportunities == 3  # toutes


def test_migration_adds_population_column(tmp_path):
    from sqlalchemy import create_engine as ce, inspect, text
    import app.database as db
    url = f"sqlite:///{tmp_path/'legacy.db'}"
    old = ce(url)
    with old.begin() as conn:
        conn.execute(text("CREATE TABLE opportunities (id INTEGER PRIMARY KEY, "
                          "establishment_name VARCHAR, establishment_type VARCHAR, "
                          "city VARCHAR, address VARCHAR, main_signal VARCHAR, "
                          "detection_date DATE, estimated_timing VARCHAR)"))
    old.dispose()
    orig_engine, orig_url = db.engine, db.DATABASE_URL
    db.engine, db.DATABASE_URL = ce(url), url
    try:
        db._run_lightweight_migrations()
        cols = {c["name"] for c in inspect(db.engine).get_columns("opportunities")}
        assert "population" in cols
    finally:
        db.engine.dispose()
        db.engine, db.DATABASE_URL = orig_engine, orig_url
