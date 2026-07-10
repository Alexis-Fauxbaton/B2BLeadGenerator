"""Endpoint du pipeline (kanban)."""
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from ..database import get_session
from ..models import STATUSES, Opportunity
from ..schemas import OpportunityList

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])


@router.get("", response_model=Dict[str, List[OpportunityList]])
def get_pipeline(session: Session = Depends(get_session),
                 population: Optional[str] = "architecte"):
    # Défaut produit (pivot 2026-07-10) : kanban ciblé architectes ;
    # ?population=chr pour le CHR, ?population= (vide) pour tout.
    query = select(Opportunity).order_by(Opportunity.opportunity_score.desc())
    if population:
        query = query.where(Opportunity.population == population)
    opportunities = session.exec(query).all()

    columns: Dict[str, List[OpportunityList]] = {status: [] for status in STATUSES}
    for opp in opportunities:
        columns.setdefault(opp.status, []).append(OpportunityList.model_validate(opp))

    return columns
