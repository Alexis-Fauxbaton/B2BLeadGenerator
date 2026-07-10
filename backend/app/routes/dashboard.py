"""Endpoint des statistiques du dashboard."""
from collections import Counter
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from ..database import get_session
from ..models import Opportunity
from ..schemas import (
    DashboardStats,
    OpportunityList,
    SignalBreakdown,
    StatusBreakdown,
)

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/stats", response_model=DashboardStats)
def get_stats(session: Session = Depends(get_session),
              population: Optional[str] = "architecte"):
    # Défaut produit (pivot 2026-07-10) : le dashboard cible les ARCHITECTES —
    # la prospection Ambient Home ne veut plus voir le CHR par défaut.
    # ?population=chr re-cible le CHR ; ?population= (vide) = toutes.
    query = select(Opportunity)
    if population:
        query = query.where(Opportunity.population == population)
    opportunities = session.exec(query).all()
    today = date.today()

    total = len(opportunities)
    hot = sum(1 for o in opportunities if o.opportunity_score >= 8)
    not_contacted = sum(1 for o in opportunities if o.status == "non_contacte")
    interested = sum(1 for o in opportunities if o.status == "interesse")
    appointments = sum(1 for o in opportunities if o.status == "rdv")
    won = sum(1 for o in opportunities if o.status == "gagne")
    lost = sum(1 for o in opportunities if o.status == "perdu")
    follow_ups_due = sum(
        1
        for o in opportunities
        if o.next_follow_up_date is not None
        and o.next_follow_up_date <= today
        and o.status not in ("gagne", "perdu")
    )

    signal_counter = Counter(o.main_signal for o in opportunities)
    status_counter = Counter(o.status for o in opportunities)

    by_signal = [
        SignalBreakdown(label=label, count=count)
        for label, count in signal_counter.most_common()
    ]
    by_status = [
        StatusBreakdown(label=label, count=count)
        for label, count in status_counter.most_common()
    ]

    hottest = sorted(
        opportunities, key=lambda o: o.opportunity_score, reverse=True
    )[:5]

    return DashboardStats(
        total_opportunities=total,
        hot_leads=hot,
        not_contacted=not_contacted,
        follow_ups_due=follow_ups_due,
        interested=interested,
        appointments=appointments,
        won=won,
        lost=lost,
        by_signal=by_signal,
        by_status=by_status,
        hottest=[OpportunityList.model_validate(o) for o in hottest],
    )
