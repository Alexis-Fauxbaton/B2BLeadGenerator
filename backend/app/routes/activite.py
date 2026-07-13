"""Vue PATRON /api/activite (admin) : journal GLOBAL des activités des closers.

Répond à la question du patron : « qui a fait quoi aujourd'hui ? ». Journal du
jour (défaut : aujourd'hui), filtrable par auteur et par jour précis, + un
compteur d'activités par closer sur la journée.

SOFT/admin : libre tant que personne n'est loggé (Alexis aujourd'hui) ; dès
qu'une session existe, elle doit être admin (un closer loggé -> 403). Cf.
`security.require_admin_soft`.
"""
from datetime import date, datetime, time, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlmodel import Session, select

from ..database import get_session
from ..models import ContactActivity, Opportunity, User
from ..schemas import ActivityJournal, ActivityJournalEntry, AuthorCount
from ..security import get_current_user, require_admin_soft

router = APIRouter(prefix="/api/activite", tags=["activite"])


def _parse_day(day: Optional[str]) -> date:
    """Jour ciblé : `day` au format YYYY-MM-DD, défaut = aujourd'hui. 422 si
    la chaîne fournie n'est pas une date ISO valide."""
    if not day:
        return date.today()
    try:
        return date.fromisoformat(day)
    except ValueError:
        raise HTTPException(
            status_code=422, detail=f"Date invalide : {day!r} (attendu YYYY-MM-DD)."
        )


@router.get("", response_model=ActivityJournal)
def get_activity_journal(
    session: Session = Depends(get_session),
    day: Optional[str] = None,
    author: Optional[str] = None,
    current_user: Optional[User] = Depends(get_current_user),
):
    """Journal du jour + compteurs par closer. `activities` respecte jour ET
    auteur ; `counts` reflète le JOUR entier (tous auteurs) pour toujours montrer
    la répartition par closer."""
    require_admin_soft(current_user)

    target = _parse_day(day)
    start = datetime.combine(target, time.min)
    end = start + timedelta(days=1)

    # --- Journal (activités + nom de fiche via jointure) ----------------------
    query = (
        select(ContactActivity, Opportunity.establishment_name)
        .join(Opportunity, ContactActivity.opportunity_id == Opportunity.id)
        .where(
            ContactActivity.created_at >= start,
            ContactActivity.created_at < end,
        )
        .order_by(ContactActivity.created_at.desc(), ContactActivity.id.desc())
    )
    if author:
        query = query.where(ContactActivity.author == author)

    activities: List[ActivityJournalEntry] = []
    for act, opp_name in session.exec(query).all():
        activities.append(
            ActivityJournalEntry(
                id=act.id,
                opportunity_id=act.opportunity_id,
                opportunity_name=opp_name,
                type=act.type,
                note=act.note,
                author=act.author,
                created_at=act.created_at,
            )
        )

    # --- Compteurs par auteur (journée entière, tous auteurs) -----------------
    count_rows = session.exec(
        select(ContactActivity.author, func.count())
        .where(
            ContactActivity.created_at >= start,
            ContactActivity.created_at < end,
        )
        .group_by(ContactActivity.author)
    ).all()
    # Tri : les plus actifs d'abord ; les sans-auteur (None) en dernier.
    counts = [AuthorCount(author=a, count=c) for a, c in count_rows]
    counts.sort(key=lambda x: (x.author is None, -x.count, x.author or ""))

    return ActivityJournal(day=target, activities=activities, counts=counts)
