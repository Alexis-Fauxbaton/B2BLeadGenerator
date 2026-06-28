"""Endpoint de génération des messages de contact."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from ..database import get_session
from ..models import ContactHistory, Opportunity, Settings
from ..schemas import GeneratedMessages
from ..services.message_generation import generate_messages

router = APIRouter(prefix="/api/opportunities", tags=["messages"])


@router.post("/{opportunity_id}/generate-messages", response_model=GeneratedMessages)
def generate(opportunity_id: int, session: Session = Depends(get_session)):
    opp = session.get(Opportunity, opportunity_id)
    if not opp:
        raise HTTPException(status_code=404, detail="Opportunité introuvable")

    settings = session.exec(select(Settings)).first()
    if not settings:
        settings = Settings()
        session.add(settings)
        session.commit()
        session.refresh(settings)

    messages = generate_messages(opp, settings)

    opp.generated_instagram_dm = messages.instagram_dm
    opp.generated_email = messages.email
    opp.generated_linkedin = messages.linkedin
    opp.generated_call_script = messages.call_script
    opp.updated_at = datetime.utcnow()
    session.add(opp)

    session.add(
        ContactHistory(
            opportunity_id=opp.id,
            channel=opp.recommended_channel,
            action_type="message_genere",
            status=opp.status,
            note=f"Messages générés ({messages.source}).",
        )
    )

    session.commit()

    return GeneratedMessages(
        instagram_dm=messages.instagram_dm,
        email=messages.email,
        linkedin=messages.linkedin,
        call_script=messages.call_script,
        source=messages.source,
    )
