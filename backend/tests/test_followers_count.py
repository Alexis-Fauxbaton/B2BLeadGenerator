"""Tests du champ `followers_count` (abonnés Instagram) : lecture depuis le
profil Apify dans les deux classifieurs, persistance + mise à jour via
_process_candidate, migration légère, sérialisation schéma. Aucun réseau."""
import json
from datetime import date
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine, select

from app.ingestion.base import LeadCandidate
from app.ingestion.instagram import classify_profiles, classify_prescripteurs
from app.ingestion.pipeline import IngestStats, _process_candidate
from app.models import Opportunity
from app.schemas import OpportunityList

SNAP = Path(__file__).resolve().parents[1] / "app" / "ingestion" / "eval" / "snapshots"
TODAY = date(2026, 7, 6)


def _engine():
    e = create_engine("sqlite://")
    SQLModel.metadata.create_all(e)
    return e


# --- Lecture depuis le profil Apify (classify_profiles / classify_prescripteurs) --


def test_classify_profiles_reads_followers_count():
    snap = json.loads((SNAP / "cafe_mokaparis.json").read_text(encoding="utf-8"))
    assert snap.get("followersCount") == 5601
    cands = [{"handle": "cafe_mokaparis", "name": "MOKA", "city": "Paris", "type": "café"}]
    out = classify_profiles(cands, {"cafe_mokaparis": snap}, client=None,
                            match_fn=None, today=TODAY)
    assert out[0]["followers_count"] == 5601


def test_classify_profiles_no_profile_data_leaves_followers_none():
    cands = [{"handle": "inconnu", "name": "Inconnu", "city": "Paris", "type": "café"}]
    out = classify_profiles(cands, {}, client=None, match_fn=None, today=TODAY)
    assert out[0]["followers_count"] is None


def test_classify_prescripteurs_reads_followers_count():
    prof = {"postsCount": 3, "biography": "Studio d'architecture d'intérieur",
            "followersCount": 842,
            "latestPosts": [{"timestamp": "2026-06-20T10:00:00.000Z"}]}
    cands = [{"handle": "studio1", "name": "Studio X", "city": "Bordeaux"}]
    out = classify_prescripteurs(cands, {"studio1": prof}, client=None,
                                 match_fn=None, today=TODAY)
    assert out[0]["followers_count"] == 842


# --- Persistance / mise à jour via _process_candidate ---------------------------


def test_process_candidate_persists_followers_count():
    with Session(_engine()) as s:
        cand = LeadCandidate(
            source="instagram", source_ref="studio1", establishment_name="Studio X",
            city="Bordeaux", address="", main_signal="prescripteur actif",
            detection_date=date(2026, 7, 10), establishment_type="architecte d'intérieur",
            population="architecte", followers_count=582,
        )
        _process_candidate(s, cand, IngestStats(source="instagram"), set(), enricher=None)
        s.commit()
        opp = s.exec(select(Opportunity).where(Opportunity.source_ref == "studio1")).first()
        assert opp is not None
        assert opp.followers_count == 582


def test_process_candidate_updates_followers_count_on_repass():
    """Un re-passage (re-scrape) avec un nouveau nombre d'abonnés MET À JOUR la
    fiche existante — comme les autres champs enrichis (VIDE > FAUX : pas figé)."""
    engine = _engine()
    with Session(engine) as s:
        cand1 = LeadCandidate(
            source="instagram", source_ref="studio1", establishment_name="Studio X",
            city="Bordeaux", address="", main_signal="prescripteur actif",
            detection_date=date(2026, 7, 10), establishment_type="architecte d'intérieur",
            population="architecte", followers_count=582,
        )
        _process_candidate(s, cand1, IngestStats(source="instagram"), set(), enricher=None)
        s.commit()

        cand2 = LeadCandidate(
            source="instagram", source_ref="studio1", establishment_name="Studio X",
            city="Bordeaux", address="", main_signal="prescripteur actif",
            detection_date=date(2026, 7, 11), establishment_type="architecte d'intérieur",
            population="architecte", followers_count=910,  # a grossi entre les 2 runs
        )
        _process_candidate(s, cand2, IngestStats(source="instagram"), set(), enricher=None)
        s.commit()

        opp = s.exec(select(Opportunity).where(Opportunity.source_ref == "studio1")).first()
        assert opp.followers_count == 910


def test_process_candidate_repass_without_followers_keeps_existing_value():
    """Un re-passage SANS followers_count (ex. profil non re-scrapé cette fois)
    NE DOIT PAS effacer la valeur déjà connue (vide > faux, mais pas régressif)."""
    engine = _engine()
    with Session(engine) as s:
        cand1 = LeadCandidate(
            source="instagram", source_ref="studio1", establishment_name="Studio X",
            city="Bordeaux", address="", main_signal="prescripteur actif",
            detection_date=date(2026, 7, 10), establishment_type="architecte d'intérieur",
            population="architecte", followers_count=582,
        )
        _process_candidate(s, cand1, IngestStats(source="instagram"), set(), enricher=None)
        s.commit()

        cand2 = LeadCandidate(
            source="instagram", source_ref="studio1", establishment_name="Studio X",
            city="Bordeaux", address="", main_signal="prescripteur actif",
            detection_date=date(2026, 7, 11), establishment_type="architecte d'intérieur",
            population="architecte", followers_count=None,
        )
        _process_candidate(s, cand2, IngestStats(source="instagram"), set(), enricher=None)
        s.commit()

        opp = s.exec(select(Opportunity).where(Opportunity.source_ref == "studio1")).first()
        assert opp.followers_count == 582


# --- Migration légère ------------------------------------------------------------


def test_migration_adds_followers_count_column(tmp_path):
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
        assert "followers_count" in cols
    finally:
        db.engine.dispose()
        db.engine, db.DATABASE_URL = orig_engine, orig_url


# --- Sérialisation schéma ---------------------------------------------------------


def test_opportunity_list_schema_exposes_followers_count():
    with Session(_engine()) as s:
        opp = Opportunity(
            establishment_name="Studio X", establishment_type="architecte d'intérieur",
            city="Bordeaux", address="", main_signal="prescripteur actif",
            detection_date=date(2026, 7, 10), estimated_timing="J-90",
            source="instagram", source_ref="studio1", population="architecte",
            instagram="studio1", followers_count=582,
        )
        s.add(opp)
        s.commit()
        s.refresh(opp)
        read = OpportunityList.model_validate(opp)
        assert read.followers_count == 582
