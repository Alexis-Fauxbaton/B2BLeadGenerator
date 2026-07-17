"""Endpoints des opportunités."""
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import exists, func
from sqlmodel import Session, select

from ..assignment import apply_assigned_filter
from ..database import get_session
from ..models import ContactActivity, ContactHistory, Opportunity, User
from ..schemas import (
    AssignmentUpdate,
    OpportunityList,
    OpportunityRead,
    OpportunityUpdate,
    PhonePromote,
    StatusUpdate,
)
from ..security import get_current_user, require_admin_soft
from ..services.phone_candidates import PromoteError, promote as _promote_phone

router = APIRouter(prefix="/api/opportunities", tags=["opportunities"])

SORT_FIELDS = {
    "score": Opportunity.opportunity_score,
    "detection_date": Opportunity.detection_date,
    "created_at": Opportunity.created_at,
    "city": Opportunity.city,
    "status": Opportunity.status,
}


@router.get("", response_model=List[OpportunityList])
def list_opportunities(
    response: Response = None,
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
    population: Optional[str] = None,
    assigned: Optional[str] = None,
    has_activity: Optional[bool] = None,
    sort_by: str = "score",
    order: str = "desc",
    limit: int = 100,
    offset: int = 0,
    current_user: Optional[User] = Depends(get_current_user),
):
    # Bornes défensives (les appels directs — tests A1 — passent des ints bruts,
    # sans la validation FastAPI Query ; on clampe donc ici).
    limit = max(1, min(500, limit))
    offset = max(0, offset)
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
    if population:
        query = query.where(Opportunity.population == population)
    if has_activity is not None:
        # « Jamais appelés » (et son inverse) : au moins une ligne dans
        # contact_activities pour cette fiche, PEU IMPORTE le statut lu/écrit.
        # Lecture seule — ne modifie rien (cf. invariant qualification, design
        # §2). Corrige l'approximation status=='non_contacte' : un lead qualifié
        # (issue quelconque, y compris émission issue=NULL) ne doit plus
        # ressortir comme « jamais appelé », même si le statut n'a pas bougé.
        has_any_activity = exists(
            select(ContactActivity.id).where(
                ContactActivity.opportunity_id == Opportunity.id
            )
        )
        query = query.where(has_any_activity if has_activity else ~has_any_activity)
    # Filtre d'assignation : me (session) | none (non assignés) | <nom du closer>.
    query = apply_assigned_filter(query, assigned, current_user)

    # Total AVANT pagination (en-tête X-Total-Count) : indispensable au pager
    # côté frontend à l'échelle du stock (~30k lignes, ~300 pages).
    total = session.exec(select(func.count()).select_from(query.subquery())).one()
    if response is not None:
        response.headers["X-Total-Count"] = str(total)

    if sort_by == "score":
        # Tri composite (volume max) : score décroissant, puis à score égal les
        # fiches CONTACTABLES (téléphone présent) avant les muettes, puis les plus
        # récentes. Sépare le hot subset (score haut) du volume sans reposer sur
        # un seuil de score brut.
        query = query.order_by(
            Opportunity.opportunity_score.desc(),
            Opportunity.phone.is_(None).asc(),
            Opportunity.detection_date.desc(),
        )
    else:
        sort_col = SORT_FIELDS.get(sort_by, Opportunity.opportunity_score)
        query = query.order_by(sort_col.desc() if order == "desc" else sort_col.asc())

    query = query.offset(offset).limit(limit)
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

    old_status = opp.status
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

    # Journal de suivi (nouveau) : trace un changement de statut effectif dans le
    # journal d'activités SOBRE (« ancien -> nouveau »). Silencieux si le statut
    # n'a pas bougé (évite le fouilli sur une simple (re)planification de relance).
    if payload.status != old_status:
        session.add(
            ContactActivity(
                opportunity_id=opp.id,
                type="statut",
                note=f"{old_status} -> {payload.status}",
            )
        )

    session.commit()
    session.refresh(opp)
    return opp


@router.patch("/{opportunity_id}/assignment", response_model=OpportunityRead)
def update_assignment(
    opportunity_id: int,
    payload: AssignmentUpdate,
    session: Session = Depends(get_session),
    current_user: Optional[User] = Depends(get_current_user),
):
    """Assigne (ou désassigne, `assigned_to=null`) un lead à un closer. Réservé
    à l'admin QUAND une session existe (soft : libre tant que personne n'est
    loggé — Alexis aujourd'hui). Un closer loggé se voit refuser (403)."""
    require_admin_soft(current_user)
    opp = session.get(Opportunity, opportunity_id)
    if not opp:
        raise HTTPException(status_code=404, detail="Opportunité introuvable")

    opp.assigned_to = payload.assigned_to
    opp.updated_at = datetime.utcnow()
    session.add(opp)
    session.commit()
    session.refresh(opp)
    return opp


@router.post("/{opportunity_id}/phones/promote", response_model=OpportunityRead)
def promote_phone(
    opportunity_id: int,
    payload: PhonePromote,
    session: Session = Depends(get_session),
    current_user: Optional[User] = Depends(get_current_user),
):
    """Promotion MANUELLE d'un numéro candidat en principal (geste du closer,
    TRACÉ) — cf. docs/plans/2026-07-17-multi-numeros-design.md §4. L'ancien
    principal (s'il existe) redescend en candidat `ex_principal`.
    `contact_confidence` reste INCHANGÉ : ce champ décrit la méthode de
    vérification de la provenance, pas une préférence humaine — la promotion
    est tracée par l'activité `note` auto-générée, pas par ce champ.

    `author` : la SESSION PRIME sur toute autre source (même politique que
    `add_activity`) — un closer loggé ne peut jamais écrire au nom d'un autre."""
    opp = session.get(Opportunity, opportunity_id)
    if not opp:
        raise HTTPException(status_code=404, detail="Opportunité introuvable")

    old_principal = opp.phone
    try:
        promoted = _promote_phone(opp, payload.number)
    except PromoteError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    opp.updated_at = datetime.utcnow()
    session.add(opp)
    author = current_user.name if isinstance(current_user, User) else None
    session.add(
        ContactActivity(
            opportunity_id=opp.id,
            type="note",
            note=(
                f"Numéro principal changé : {old_principal or 'aucun'} → "
                f"{promoted['number']} (source {promoted['source']})"
            ),
            author=author,
        )
    )
    session.commit()
    session.refresh(opp)
    return opp
