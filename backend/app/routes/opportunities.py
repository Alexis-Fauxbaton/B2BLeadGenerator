"""Endpoints des opportunités."""
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from ..database import get_session
from ..models import ContactHistory, Opportunity
from ..schemas import (
    OpportunityList,
    OpportunityRead,
    OpportunityUpdate,
    StatusUpdate,
)

router = APIRouter(prefix="/api/opportunities", tags=["opportunities"])

SORT_FIELDS = {
    "score": Opportunity.opportunity_score,
    "detection_date": Opportunity.detection_date,
    "city": Opportunity.city,
    "status": Opportunity.status,
}


@router.get("", response_model=List[OpportunityList])
def list_opportunities(
    session: Session = Depends(get_session),
    search: Optional[str] = None,
    city: Optional[str] = None,
    establishment_type: Optional[str] = None,
    main_signal: Optional[str] = None,
    status: Optional[str] = None,
    min_score: Optional[int] = None,
    recommended_channel: Optional[str] = None,
    source: Optional[str] = None,
    lifecycle_label: Optional[str] = None,
    sort_by: str = "score",
    order: str = "desc",
):
    query = select(Opportunity)

    if search:
        query = query.where(Opportunity.establishment_name.ilike(f"%{search}%"))
    if city:
        query = query.where(Opportunity.city == city)
    if establishment_type:
        query = query.where(Opportunity.establishment_type == establishment_type)
    if main_signal:
        query = query.where(Opportunity.main_signal == main_signal)
    if status:
        query = query.where(Opportunity.status == status)
    if min_score is not None:
        query = query.where(Opportunity.opportunity_score >= min_score)
    if recommended_channel:
        query = query.where(Opportunity.recommended_channel == recommended_channel)
    if source:
        query = query.where(Opportunity.source == source)
    if lifecycle_label:
        query = query.where(Opportunity.lifecycle_label == lifecycle_label)

    sort_col = SORT_FIELDS.get(sort_by, Opportunity.opportunity_score)
    query = query.order_by(sort_col.desc() if order == "desc" else sort_col.asc())

    return session.exec(query).all()


@router.get("/{opportunity_id}", response_model=OpportunityRead)
def get_opportunity(opportunity_id: int, session: Session = Depends(get_session)):
    opp = session.get(Opportunity, opportunity_id)
    if not opp:
        raise HTTPException(status_code=404, detail="Opportunité introuvable")
    return opp


@router.patch("/{opportunity_id}", response_model=OpportunityRead)
def update_opportunity(
    opportunity_id: int,
    payload: OpportunityUpdate,
    session: Session = Depends(get_session),
):
    opp = session.get(Opportunity, opportunity_id)
    if not opp:
        raise HTTPException(status_code=404, detail="Opportunité introuvable")

    data = payload.model_dump(exclude_unset=True)
    note = data.pop("note", None)

    for key, value in data.items():
        setattr(opp, key, value)
    opp.updated_at = datetime.utcnow()

    session.add(opp)

    if note:
        session.add(
            ContactHistory(
                opportunity_id=opp.id,
                action_type="note",
                status=opp.status,
                note=note,
            )
        )

    session.commit()
    session.refresh(opp)
    return opp


@router.patch("/{opportunity_id}/status", response_model=OpportunityRead)
def update_status(
    opportunity_id: int,
    payload: StatusUpdate,
    session: Session = Depends(get_session),
):
    opp = session.get(Opportunity, opportunity_id)
    if not opp:
        raise HTTPException(status_code=404, detail="Opportunité introuvable")

    opp.status = payload.status
    if payload.next_follow_up_date is not None:
        opp.next_follow_up_date = payload.next_follow_up_date
    opp.updated_at = datetime.utcnow()
    session.add(opp)

    action_type = "relance_planifiee" if payload.next_follow_up_date else "statut_change"
    session.add(
        ContactHistory(
            opportunity_id=opp.id,
            channel=opp.recommended_channel,
            action_type=action_type,
            status=payload.status,
            note=payload.note,
            contacted_at=datetime.utcnow(),
        )
    )

    session.commit()
    session.refresh(opp)
    return opp
