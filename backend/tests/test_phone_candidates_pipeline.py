"""Producteurs `pipeline.py` du chantier multi-numéros (TDD, aucun réseau) :
`_merge_corroboration` (cross-fill, corroboration SIREN registre × instagram)
pousse le téléphone de l'AUTRE source en candidat au lieu de le jeter. Les cas
« annuaire diffère / no-op si égal » (upsert même-source + fusion douce
nom+ville) sont couverts dans tests/test_run_annuaires.py (branches voisines
du même chantier). Cf. docs/plans/2026-07-17-multi-numeros-design.md §2.3."""
from datetime import date

from sqlmodel import Session, SQLModel, create_engine, select

from app.ingestion.base import LeadCandidate
from app.ingestion.pipeline import IngestStats, _merge_corroboration, _process_candidate
from app.models import Opportunity


def _engine():
    e = create_engine("sqlite://")
    SQLModel.metadata.create_all(e)
    return e


def test_merge_corroboration_direct_adds_cross_fill_candidate_when_phone_differs():
    with Session(_engine()) as s:
        opp = Opportunity(
            establishment_name="Atelier Y", establishment_type="architecte d'intérieur",
            city="Lyon", address="", main_signal="prescripteur actif",
            detection_date=date(2026, 7, 11), estimated_timing="J-90",
            source="instagram", source_ref="atelier_y_insta", population="architecte",
            phone="01 00 00 00 01",
        )
        s.add(opp)
        s.commit()
        s.refresh(opp)

        cand = LeadCandidate(
            source="places", source_ref="places:gp42",
            establishment_name="Atelier Y", city="Lyon", address="9 rue X 69001 Lyon",
            main_signal="prescripteur actif", detection_date=date(2026, 7, 11),
            establishment_type="architecte d'intérieur", population="architecte",
            raw={"phone": "01 00 00 00 02"},  # DIFFÉRENT du principal
        )
        _merge_corroboration(s, opp, cand)
        s.commit()
        s.refresh(opp)

        assert opp.phone == "01 00 00 00 01"  # jamais écrasé
        assert [c["number"] for c in opp.phone_candidates] == ["01 00 00 00 02"]
        assert opp.phone_candidates[0]["source"] == "cross_fill"


def test_merge_corroboration_direct_no_candidate_when_phone_absent():
    with Session(_engine()) as s:
        opp = Opportunity(
            establishment_name="Atelier Z", establishment_type="architecte d'intérieur",
            city="Lyon", address="", main_signal="prescripteur actif",
            detection_date=date(2026, 7, 11), estimated_timing="J-90",
            source="instagram", source_ref="atelier_z_insta", population="architecte",
            phone="01 00 00 00 01",
        )
        s.add(opp)
        s.commit()

        cand = LeadCandidate(
            source="bodacc", source_ref="bodacc:1",
            establishment_name="Atelier Z", city="Lyon", address="",
            main_signal="reprise", detection_date=date(2026, 7, 11),
            establishment_type="architecte d'intérieur", population="architecte",
        )
        _merge_corroboration(s, opp, cand)
        s.commit()
        s.refresh(opp)
        assert opp.phone_candidates == []


def test_merge_corroboration_direct_no_candidate_when_phone_equals_principal():
    with Session(_engine()) as s:
        opp = Opportunity(
            establishment_name="Atelier W", establishment_type="architecte d'intérieur",
            city="Lyon", address="", main_signal="prescripteur actif",
            detection_date=date(2026, 7, 11), estimated_timing="J-90",
            source="instagram", source_ref="atelier_w_insta", population="architecte",
            phone="01 00 00 00 01",
        )
        s.add(opp)
        s.commit()

        cand = LeadCandidate(
            source="places", source_ref="places:gp99",
            establishment_name="Atelier W", city="Lyon", address="",
            main_signal="prescripteur actif", detection_date=date(2026, 7, 11),
            establishment_type="architecte d'intérieur", population="architecte",
            raw={"phone": "01.00.00.00.01"},  # MÊME numéro, format différent
        )
        _merge_corroboration(s, opp, cand)
        s.commit()
        s.refresh(opp)
        assert opp.phone_candidates == []


def test_process_candidate_siren_corroboration_pushes_cross_fill_candidate():
    """Bout en bout via `_process_candidate` (branche SIREN, ~L1165) : la
    fusion registre × instagram ne perd plus le téléphone de l'autre source."""
    with Session(_engine()) as s:
        _process_candidate(s, LeadCandidate(
            source="instagram", source_ref="atelier_y_insta",
            establishment_name="Atelier Y", city="Lyon", address="",
            main_signal="prescripteur actif", detection_date=date(2026, 7, 11),
            establishment_type="architecte d'intérieur", population="architecte",
            siren="111222333"),
            IngestStats(source="instagram"), set(), None)
        s.commit()
        opp = s.exec(select(Opportunity)).first()
        opp.phone = "01 00 00 00 01"
        s.add(opp)
        s.commit()

        stats = IngestStats(source="places")
        _process_candidate(s, LeadCandidate(
            source="places", source_ref="places:gp42",
            establishment_name="Atelier Y", city="Lyon",
            address="9 rue X 69001 Lyon",
            main_signal="prescripteur actif", detection_date=date(2026, 7, 11),
            establishment_type="architecte d'intérieur", population="architecte",
            siren="111222333",  # même SIREN -> corroboration
            raw={"phone": "01 00 00 00 02"}),  # DIFFÉRENT
            stats, set(), None)
        s.commit()
        s.refresh(opp)

        assert stats.updated == 1
        assert opp.phone == "01 00 00 00 01"  # principal inchangé
        assert [c["number"] for c in opp.phone_candidates] == ["01 00 00 00 02"]
        assert opp.phone_candidates[0]["source"] == "cross_fill"
