"""Suivi de contact SOBRE : journal d'activités par fiche + prochaine action.

Volontairement léger (critère d'acceptation « pas le fouilli ») : quatre gestes
rapides (appel / email / dm_insta / note) + le journal AUTO des changements de
statut (dans routes/opportunities.py) écrivent dans `contact_activities` ; UNE
prochaine action (texte court + date) se pose/s'efface d'un coup.
"""
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from ..database import get_session
from ..models import ACTIVITY_TYPES, ContactActivity, Opportunity
from ..schemas import (
    ContactActivityCreate,
    ContactActivityRead,
    NextActionUpdate,
    OpportunityRead,
)

router = APIRouter(prefix="/api/opportunities", tags=["activities"])


@router.get("/{opportunity_id}/activities", response_model=List[ContactActivityRead])
def list_activities(
    opportunity_id: int,
    session: Session = Depends(get_session),
    limit: int = 50,
    offset: int = 0,
):
    """Journal d'une fiche, du plus récent au plus ancien (pagination légère :
    la fiche plie l'historique au-delà de 5 entrées côté UI)."""
    if not session.get(Opportunity, opportunity_id):
        raise HTTPException(status_code=404, detail="Opportunité introuvable")

    limit = max(1, min(200, limit))
    offset = max(0, offset)
    query = (
        select(ContactActivity)
        .where(ContactActivity.opportunity_id == opportunity_id)
        .order_by(ContactActivity.created_at.desc(), ContactActivity.id.desc())
        .offset(offset)
        .limit(limit)
    )
    return session.exec(query).all()


@router.post(
    "/{opportunity_id}/activities",
    response_model=ContactActivityRead,
    status_code=201,
)
def add_activity(
    opportunity_id: int,
    payload: ContactActivityCreate,
    session: Session = Depends(get_session),
):
    """Enregistre un geste rapide et touche `updated_at` (l'activité fait vivre
    la fiche). N'altère JAMAIS le statut (découplé du changement de statut)."""
    opp = session.get(Opportunity, opportunity_id)
    if not opp:
        raise HTTPException(status_code=404, detail="Opportunité introuvable")
    if payload.type not in ACTIVITY_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Type d'activité inconnu : {payload.type!r} "
            f"(attendu {ACTIVITY_TYPES}).",
        )

    activity = ContactActivity(
        opportunity_id=opp.id, type=payload.type, note=payload.note
    )
    session.add(activity)
    opp.updated_at = datetime.utcnow()
    session.add(opp)
    session.commit()
    session.refresh(activity)
    return activity


@router.put("/{opportunity_id}/next-action", response_model=OpportunityRead)
def set_next_action(
    opportunity_id: int,
    payload: NextActionUpdate,
    session: Session = Depends(get_session),
):
    """UNE prochaine action (texte + date), remplacement complet : les deux
    champs sont posés ensemble et effaçables (`{}` => les deux à null)."""
    opp = session.get(Opportunity, opportunity_id)
    if not opp:
        raise HTTPException(status_code=404, detail="Opportunité introuvable")

    opp.next_action = payload.next_action
    opp.next_follow_up_date = payload.next_follow_up_date
    opp.updated_at = datetime.utcnow()
    session.add(opp)
    session.commit()
    session.refresh(opp)
    return opp
