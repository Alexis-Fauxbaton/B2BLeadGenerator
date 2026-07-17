"""Endpoint POST /api/opportunities/{id}/phones/promote (chantier multi-numéros),
TDD, aucun réseau. Appelé en direct (comme test_contact_activities.py) : session
en argument explicite, `current_user` optionnel (auth « soft », session prime).
Cf. docs/plans/2026-07-17-multi-numeros-design.md §4."""
from datetime import date, datetime

import pytest
from fastapi import HTTPException
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import ContactActivity, Opportunity, User
from app.routes.opportunities import promote_phone
from app.schemas import OpportunityList, PhonePromote


def _engine():
    e = create_engine("sqlite://")
    SQLModel.metadata.create_all(e)
    return e


def _opp(session, phone="02 49 88 42 85", candidates=None):
    opp = Opportunity(
        establishment_name="Studio X", establishment_type="architecte d'intérieur",
        city="Nantes", address="", main_signal="prescripteur actif",
        detection_date=date(2026, 7, 1), estimated_timing="J-90",
        population="architecte", phone=phone, phone_candidates=candidates or [],
        contact_confidence="moyenne",
    )
    session.add(opp)
    session.commit()
    session.refresh(opp)
    return opp


def test_promote_swaps_principal_200():
    with Session(_engine()) as s:
        opp = _opp(s, candidates=[
            {"number": "02 85 52 84 93", "source": "site", "first_seen": "2026-07-10"},
        ])
        result = promote_phone(opp.id, PhonePromote(number="02 85 52 84 93"), s)
        assert result.phone == "02 85 52 84 93"
        assert [c["number"] for c in result.phone_candidates] == ["02 49 88 42 85"]
        assert result.phone_candidates[0]["source"] == "ex_principal"


def test_promote_leaves_contact_confidence_intact():
    with Session(_engine()) as s:
        opp = _opp(s, candidates=[
            {"number": "02 85 52 84 93", "source": "site", "first_seen": "2026-07-10"},
        ])
        assert opp.contact_confidence == "moyenne"
        result = promote_phone(opp.id, PhonePromote(number="02 85 52 84 93"), s)
        assert result.contact_confidence == "moyenne"  # inchangé (§4.1)


def test_promote_creates_note_activity_with_source_and_numbers():
    with Session(_engine()) as s:
        opp = _opp(s, candidates=[
            {"number": "02 85 52 84 93", "source": "site", "first_seen": "2026-07-10"},
        ])
        promote_phone(opp.id, PhonePromote(number="02 85 52 84 93"), s)
        acts = s.exec(
            select(ContactActivity).where(ContactActivity.opportunity_id == opp.id)
        ).all()
        assert len(acts) == 1
        act = acts[0]
        assert act.type == "note"
        assert act.issue is None and act.raison is None  # pas une qualification
        assert "02 49 88 42 85" in act.note and "02 85 52 84 93" in act.note
        assert "site" in act.note


def test_promote_activity_author_is_session_user():
    with Session(_engine()) as s:
        opp = _opp(s, candidates=[
            {"number": "02 85 52 84 93", "source": "annuaire", "first_seen": "2026-07-10"},
        ])
        user = User(name="Marie", email="marie@lumapro.fr", password_hash="x", role="closer")
        promote_phone(opp.id, PhonePromote(number="02 85 52 84 93"), s, current_user=user)
        act = s.exec(
            select(ContactActivity).where(ContactActivity.opportunity_id == opp.id)
        ).first()
        assert act.author == "Marie"


def test_promote_touches_updated_at():
    with Session(_engine()) as s:
        opp = _opp(s, candidates=[
            {"number": "02 85 52 84 93", "source": "site", "first_seen": "2026-07-10"},
        ])
        opp.updated_at = datetime(2020, 1, 1)
        s.add(opp)
        s.commit()
        promote_phone(opp.id, PhonePromote(number="02 85 52 84 93"), s)
        s.refresh(opp)
        assert opp.updated_at > datetime(2020, 1, 1)


def test_promote_422_when_number_not_a_candidate():
    with Session(_engine()) as s:
        opp = _opp(s, candidates=[])
        with pytest.raises(HTTPException) as exc:
            promote_phone(opp.id, PhonePromote(number="02 85 52 84 93"), s)
        assert exc.value.status_code == 422
        # Rien n'a bougé (fail-fast, pas d'effet de bord partiel).
        s.refresh(opp)
        assert opp.phone == "02 49 88 42 85"
        acts = s.exec(
            select(ContactActivity).where(ContactActivity.opportunity_id == opp.id)
        ).all()
        assert acts == []


def test_promote_404_on_missing_opp():
    with Session(_engine()) as s:
        with pytest.raises(HTTPException) as exc:
            promote_phone(999, PhonePromote(number="02 85 52 84 93"), s)
        assert exc.value.status_code == 404


# --- Sérialisation OpportunityList (candidats dans la fiche) --------------------


def test_opportunity_list_serializes_phone_candidates():
    with Session(_engine()) as s:
        opp = _opp(s, candidates=[
            {"number": "02 85 52 84 93", "source": "site", "first_seen": "2026-07-10"},
        ])
        read = OpportunityList.model_validate(opp)
        assert read.phone_candidates == [
            {"number": "02 85 52 84 93", "source": "site", "first_seen": "2026-07-10"},
        ]


def test_opportunity_list_coerces_null_phone_candidates_to_empty_list():
    # Ligne pré-migration (NULL en base) : le validateur coerce en [] plutôt
    # que de casser la sérialisation (même pattern qu'extra_addresses/extra_emails).
    with Session(_engine()) as s:
        opp = _opp(s, candidates=[])
        opp.phone_candidates = None
        s.add(opp)
        s.commit()
        s.refresh(opp)
        read = OpportunityList.model_validate(opp)
        assert read.phone_candidates == []


def test_promote_without_prior_principal():
    with Session(_engine()) as s:
        opp = _opp(s, phone=None, candidates=[
            {"number": "02 85 52 84 93", "source": "annuaire", "first_seen": "2026-07-10"},
        ])
        result = promote_phone(opp.id, PhonePromote(number="02 85 52 84 93"), s)
        assert result.phone == "02 85 52 84 93"
        assert result.phone_candidates == []  # pas d'ex_principal (rien avant)
        act = s.exec(
            select(ContactActivity).where(ContactActivity.opportunity_id == opp.id)
        ).first()
        assert "aucun" in act.note
