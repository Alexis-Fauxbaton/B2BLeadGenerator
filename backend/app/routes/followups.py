"""Vue « À relancer » : fiches groupées par échéance + compteur pour le badge.

Buckets (relatifs à aujourd'hui) :
- en_retard      : next_follow_up_date  <  aujourd'hui
- aujourdhui     : next_follow_up_date  == aujourd'hui
- cette_semaine  : aujourd'hui < next_follow_up_date <= aujourd'hui + 7 jours

Au-delà de 7 jours : pas encore « à relancer » (exclu de la vue et du compteur).
Exclut toujours les fiches gagne/perdu. Par défaut ciblé population=architecte
(cohérent avec le dashboard, pivot Ambient Home) ; ?population= (vide) = toutes.
"""
from datetime import date, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from ..assignment import apply_assigned_filter
from ..database import get_session
from ..models import Opportunity, User
from ..schemas import FollowUpBuckets, FollowUpCount, OpportunityList
from ..security import get_current_user

router = APIRouter(prefix="/api/followups", tags=["followups"])

# Statuts terminaux : jamais « à relancer ».
_CLOSED = ("gagne", "perdu")


def _due_opportunities(
    session: Session,
    population: Optional[str],
    assigned: Optional[str] = None,
    current_user=None,
) -> List[Opportunity]:
    """Fiches avec une échéance <= aujourd'hui + 7 jours, hors gagne/perdu,
    triées par échéance croissante (urgent d'abord) puis score décroissant.
    `assigned` (me|none|<nom>) filtre par closer (« Mes relances »)."""
    horizon = date.today() + timedelta(days=7)
    query = select(Opportunity).where(
        Opportunity.next_follow_up_date.is_not(None),
        Opportunity.next_follow_up_date <= horizon,
        Opportunity.status.not_in(_CLOSED),
    )
    if population:
        query = query.where(Opportunity.population == population)
    query = apply_assigned_filter(query, assigned, current_user)
    query = query.order_by(
        Opportunity.next_follow_up_date.asc(),
        Opportunity.opportunity_score.desc(),
    )
    return session.exec(query).all()


def _bucketize(opportunities: List[Opportunity]):
    today = date.today()
    en_retard, aujourdhui, cette_semaine = [], [], []
    for o in opportunities:
        d = o.next_follow_up_date
        if d < today:
            en_retard.append(o)
        elif d == today:
            aujourdhui.append(o)
        else:  # today < d <= today + 7 (garanti par la requête)
            cette_semaine.append(o)
    return en_retard, aujourdhui, cette_semaine


@router.get("", response_model=FollowUpBuckets)
def get_follow_ups(
    session: Session = Depends(get_session),
    population: Optional[str] = "architecte",
    assigned: Optional[str] = None,
    current_user: Optional[User] = Depends(get_current_user),
):
    en_retard, aujourdhui, cette_semaine = _bucketize(
        _due_opportunities(session, population, assigned, current_user)
    )
    return FollowUpBuckets(
        en_retard=[OpportunityList.model_validate(o) for o in en_retard],
        aujourdhui=[OpportunityList.model_validate(o) for o in aujourdhui],
        cette_semaine=[OpportunityList.model_validate(o) for o in cette_semaine],
    )


@router.get("/count", response_model=FollowUpCount)
def get_follow_ups_count(
    session: Session = Depends(get_session),
    population: Optional[str] = "architecte",
    assigned: Optional[str] = None,
    current_user: Optional[User] = Depends(get_current_user),
):
    """Compteur léger pour le badge discret de la nav (aucune sérialisation de
    fiche). `total` = en_retard + aujourdhui + cette_semaine. Respecte
    `assigned=me` : le badge d'un closer loggé ne compte QUE ses relances."""
    en_retard, aujourdhui, cette_semaine = _bucketize(
        _due_opportunities(session, population, assigned, current_user)
    )
    return FollowUpCount(
        en_retard=len(en_retard),
        aujourdhui=len(aujourdhui),
        cette_semaine=len(cette_semaine),
        total=len(en_retard) + len(aujourdhui) + len(cette_semaine),
    )
