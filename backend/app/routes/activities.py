"""Suivi de contact SOBRE : journal d'activités par fiche + prochaine action.

Volontairement léger (critère d'acceptation « pas le fouilli ») : quatre gestes
rapides (appel / email / dm_insta / note) + le journal AUTO des changements de
statut (dans routes/opportunities.py) écrivent dans `contact_activities` ; UNE
prochaine action (texte court + date) se pose/s'efface d'un coup.
"""
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from ..database import get_session
from ..models import (
    ACTIVITY_TYPES,
    QUALIF_DETAILS,
    QUALIF_ISSUES,
    QUALIF_RAISONS,
    ContactActivity,
    Opportunity,
    User,
)
from ..schemas import (
    ContactActivityCreate,
    ContactActivityDetailUpdate,
    ContactActivityRead,
    LastIssue,
    NextActionUpdate,
    OpportunityRead,
)
from ..security import get_current_user

router = APIRouter(prefix="/api/opportunities", tags=["activities"])


def _validate_detail(detail: List[str]) -> None:
    """Valide le N3 (`detail`, chips libres) contre `QUALIF_DETAILS` — factorisé
    car réutilisé par la création (`add_activity`) ET l'enrichissement a
    posteriori (`update_activity_detail`)."""
    invalid_details = [d for d in detail if d not in QUALIF_DETAILS]
    if invalid_details:
        raise HTTPException(
            status_code=422,
            detail=f"Détail(s) inconnu(s) : {invalid_details} (attendu {QUALIF_DETAILS}).",
        )


def _validate_qualification(payload: ContactActivityCreate) -> None:
    """Valide la qualification N1/N2/N3 (`issue`/`raison`/`detail`), TOUS
    optionnels. Même politique que la validation `type` existante : 422 sur
    combo invalide. N'écrit jamais rien — c'est une porte d'entrée seulement."""
    if payload.issue is not None and payload.issue not in QUALIF_ISSUES:
        raise HTTPException(
            status_code=422,
            detail=f"Issue inconnue : {payload.issue!r} (attendu {QUALIF_ISSUES}).",
        )
    if payload.raison is not None:
        allowed = QUALIF_RAISONS.get((payload.type, payload.issue), [])
        if payload.raison not in allowed:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Raison inconnue pour type={payload.type!r}/"
                    f"issue={payload.issue!r} : {payload.raison!r} (attendu {allowed})."
                ),
            )
    _validate_detail(payload.detail)


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
    current_user: Optional[User] = Depends(get_current_user),
):
    """Enregistre un geste rapide et touche `updated_at` (l'activité fait vivre
    la fiche). N'altère JAMAIS le statut (découplé du changement de statut).

    `author` : la SESSION PRIME sur le body — un closer loggé ne peut pas écrire
    au nom d'un autre (identité vide > fausse identité). Le body n'est retenu que
    si personne n'est loggé (app ouverte sans compte, cf. auth « soft »)."""
    opp = session.get(Opportunity, opportunity_id)
    if not opp:
        raise HTTPException(status_code=404, detail="Opportunité introuvable")
    if payload.type not in ACTIVITY_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Type d'activité inconnu : {payload.type!r} "
            f"(attendu {ACTIVITY_TYPES}).",
        )
    _validate_qualification(payload)

    # isinstance et non `is not None` : appelée en direct dans les tests, la valeur
    # par défaut est l'objet Depends (sentinelle), pas None -> on ne la traite comme
    # une session que si c'est bien un User.
    author = current_user.name if isinstance(current_user, User) else payload.author
    activity = ContactActivity(
        opportunity_id=opp.id,
        type=payload.type,
        note=payload.note,
        author=author,
        issue=payload.issue,
        raison=payload.raison,
        detail=payload.detail,
        contact_used=payload.contact_used,
    )
    session.add(activity)
    # Une qualification fait vivre la fiche comme n'importe quel geste — mais NE
    # touche JAMAIS `status` ni aucun autre champ métier (invariant du design :
    # « on monitore, on ne nourrit pas la donnée »).
    opp.updated_at = datetime.utcnow()
    session.add(opp)
    session.commit()
    session.refresh(activity)
    return activity


@router.patch(
    "/{opportunity_id}/activities/{activity_id}/detail",
    response_model=ContactActivityRead,
)
def update_activity_detail(
    opportunity_id: int,
    activity_id: int,
    payload: ContactActivityDetailUpdate,
    session: Session = Depends(get_session),
):
    """Enrichit le N3 (`detail`/`note`) d'une qualification déjà postée, SANS
    créer de doublon. Cas d'usage : le closer tape le preset rapide (1 tap =
    1 POST, issue+raison) puis rouvre « + Détail » pour préciser — au lieu de
    reposter une 2ᵉ activité avec le même (issue, raison), le front rattache le
    détail à l'activité déjà créée via cet endpoint.

    Ne touche JAMAIS `type`/`issue`/`raison`/`author` (déjà posés, immuables
    ici) ni le statut de la fiche — même invariant que `add_activity`. Champs
    omis du payload = inchangés (PATCH partiel)."""
    opp = session.get(Opportunity, opportunity_id)
    if not opp:
        raise HTTPException(status_code=404, detail="Opportunité introuvable")
    activity = session.get(ContactActivity, activity_id)
    if not activity or activity.opportunity_id != opportunity_id:
        raise HTTPException(status_code=404, detail="Activité introuvable")

    if payload.detail is not None:
        _validate_detail(payload.detail)
        activity.detail = payload.detail
    if payload.note is not None:
        activity.note = payload.note
    session.add(activity)
    opp.updated_at = datetime.utcnow()
    session.add(opp)
    session.commit()
    session.refresh(activity)
    return activity


@router.get("/last-issues", response_model=Dict[int, LastIssue])
def get_last_issues(
    ids: str,
    session: Session = Depends(get_session),
):
    """Dernière issue connue par fiche, pour les puces « dernier contact » des
    listes (§2.2 du design) — DÉRIVÉE à la volée, jamais persistée sur la fiche.

    `ids` : liste d'identifiants séparés par des virgules (ids de la page
    courante ; endpoint batch pour éviter le N+1). Une seule requête triée par
    date décroissante ; on ne garde que la première ligne rencontrée par fiche.
    Enregistré ICI (et non sous `/{opportunity_id}/...`) : chemin littéral, doit
    être routé avant `GET /api/opportunities/{opportunity_id}` (cf. ordre
    d'inclusion des routers dans main.py)."""
    try:
        opp_ids = [int(x) for x in ids.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"ids invalide : {ids!r} (attendu une liste d'entiers séparés par des virgules).",
        )
    if not opp_ids:
        return {}

    rows = session.exec(
        select(ContactActivity)
        .where(
            ContactActivity.opportunity_id.in_(opp_ids),
            ContactActivity.issue.is_not(None),
        )
        .order_by(ContactActivity.created_at.desc(), ContactActivity.id.desc())
    ).all()

    result: Dict[int, LastIssue] = {}
    for row in rows:
        if row.opportunity_id in result:
            continue  # déjà vu une ligne plus récente pour cette fiche
        result[row.opportunity_id] = LastIssue(
            opportunity_id=row.opportunity_id,
            issue=row.issue,
            raison=row.raison,
            at=row.created_at,
        )
    return result


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
