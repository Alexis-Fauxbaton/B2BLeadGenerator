"""Vue PATRON /api/activite (admin) : journal GLOBAL des activités des closers.

Répond à la question du patron : « qui a fait quoi aujourd'hui ? ». Journal du
jour (défaut : aujourd'hui), filtrable par auteur et par jour précis, + un
compteur d'activités par closer sur la journée.

SOFT/admin : libre tant que personne n'est loggé (Alexis aujourd'hui) ; dès
qu'une session existe, elle doit être admin (un closer loggé -> 403). Cf.
`security.require_admin_soft`.
"""
from datetime import date, datetime, time, timedelta
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlmodel import Session, select

from ..database import get_session
from ..models import ContactActivity, Opportunity, User
from ..schemas import (
    ActivityJournal,
    ActivityJournalEntry,
    AuthorCount,
    QualifChannelStats,
    QualifCloserStats,
    QualifDailyVolume,
    QualifKoReason,
    QualifKpis,
    QualifStats,
)
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


# --- /stats : monitoring des résultats de qualification (100 % lecture) -------
# Onglet « Résultats » de /activite (§2 du design qualification). Un geste de
# qualification (issue/raison/detail) est enregistré et agrégé ICI ; il ne
# modifie JAMAIS `Opportunity` — invariant non négociable du design.


# Largeur max d'une plage de dates libres (§2.1) : borne défensive — au-delà,
# `daily_call_volume` (boucle jour par jour, §2.1 point 6) génèrerait une
# réponse démesurée pour une vue de monitoring qui n'a pas vocation à couvrir
# plusieurs années. Généreux (≈ 3 ans) pour ne jamais gêner un usage légitime.
_MAX_PERIOD_DAYS = 1100


def _resolve_period(
    period: Optional[str], start: Optional[str], end: Optional[str]
) -> "tuple[date, date]":
    """Résout la période ciblée par la vue Résultats. Priorité aux dates libres
    (`start`/`end`, YYYY-MM-DD) ; sinon un preset `period` ('7j' | '30j' | tout
    le reste -> "aujourd'hui", le défaut). 422 si les dates fournies sont
    invalides, incohérentes (`end` avant `start`) ou trop larges
    (> `_MAX_PERIOD_DAYS`)."""
    today = date.today()
    if start or end:
        try:
            period_start = date.fromisoformat(start) if start else today
            period_end = date.fromisoformat(end) if end else today
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail=f"Date invalide : start={start!r} end={end!r} (attendu YYYY-MM-DD).",
            )
        if period_end < period_start:
            raise HTTPException(
                status_code=422, detail="Période invalide : end doit être >= start."
            )
        if (period_end - period_start).days > _MAX_PERIOD_DAYS:
            raise HTTPException(
                status_code=422,
                detail=f"Période trop large : {_MAX_PERIOD_DAYS} jours maximum.",
            )
        return period_start, period_end
    if period == "7j":
        return today - timedelta(days=6), today
    if period == "30j":
        return today - timedelta(days=29), today
    return today, today  # défaut : "Aujourd'hui"


def _joignabilite(rows: List[ContactActivity]) -> Optional[float]:
    """joint / (joint+pas_joint+ko) sur un jeu de lignes déjà qualifiées (`issue`
    non nul). None (pas 0.0) quand il n'y a aucune tentative — distingue « zéro
    joignabilité » de « pas de données »."""
    total = len(rows)
    if total == 0:
        return None
    joints = sum(1 for r in rows if r.issue == "joint")
    return joints / total


@router.get("/stats", response_model=QualifStats)
def get_qualif_stats(
    session: Session = Depends(get_session),
    period: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    current_user: Optional[User] = Depends(get_current_user),
):
    """Agrégats de monitoring : KPIs (tentatives/joignabilité/volume d'appels/
    réponses email+DM), par closer, par canal, top raisons de KO, volume
    d'appels/jour. Mêmes gardes que le journal (admin SOFT)."""
    require_admin_soft(current_user)

    period_start, period_end = _resolve_period(period, start, end)
    range_start = datetime.combine(period_start, time.min)
    range_end = datetime.combine(period_end, time.min) + timedelta(days=1)

    def _in_range(query):
        return query.where(
            ContactActivity.created_at >= range_start,
            ContactActivity.created_at < range_end,
        )

    # Lignes avec un résultat connu (issue non nul) sur la période : base de
    # calcul des KPIs / par closer / par canal / top KO.
    qualified: List[ContactActivity] = session.exec(
        _in_range(select(ContactActivity)).where(ContactActivity.issue.is_not(None))
    ).all()
    # Tous les appels de la période (résultat connu ou pas) : indicateur de
    # rythme, distinct des « tentatives » (qui exigent un résultat).
    call_rows: List[ContactActivity] = session.exec(
        _in_range(select(ContactActivity)).where(ContactActivity.type == "appel")
    ).all()

    # --- KPIs -------------------------------------------------------------
    kpis = QualifKpis(
        tentatives=len(qualified),
        joignabilite=_joignabilite(qualified),
        volume_appels=len(call_rows),
        reponses_email_dm=sum(1 for r in qualified if r.type in ("email", "dm_insta")),
    )

    # --- Par closer ---------------------------------------------------------
    by_author: Dict[Optional[str], List[ContactActivity]] = {}
    for r in qualified:
        by_author.setdefault(r.author, []).append(r)
    by_closer = [
        QualifCloserStats(
            closer=author,
            tentatives=len(rows),
            joints=sum(1 for r in rows if r.issue == "joint"),
            joignabilite=_joignabilite(rows),
        )
        for author, rows in by_author.items()
    ]
    by_closer.sort(key=lambda c: (c.closer is None, -c.tentatives, c.closer or ""))

    # --- Par canal ------------------------------------------------------------
    by_type: Dict[str, List[ContactActivity]] = {}
    for r in qualified:
        by_type.setdefault(r.type, []).append(r)
    by_channel = [
        QualifChannelStats(
            type=t,
            tentatives=len(rows),
            joints=sum(1 for r in rows if r.issue == "joint"),
            joignabilite=_joignabilite(rows),
        )
        for t, rows in by_type.items()
    ]
    by_channel.sort(key=lambda c: (-c.tentatives, c.type))

    # --- Top raisons de KO (5 max) ---------------------------------------------
    ko_counts: Dict[str, int] = {}
    for r in qualified:
        if r.issue == "ko" and r.raison:
            ko_counts[r.raison] = ko_counts.get(r.raison, 0) + 1
    top_ko_reasons = sorted(
        (QualifKoReason(raison=raison, count=count) for raison, count in ko_counts.items()),
        key=lambda x: (-x.count, x.raison),
    )[:5]

    # --- Volume d'appels par jour (tous les jours de la période, 0 comblé) ----
    by_day: Dict[date, int] = {}
    for r in call_rows:
        d = r.created_at.date()
        by_day[d] = by_day.get(d, 0) + 1
    daily_call_volume = []
    d = period_start
    while d <= period_end:
        daily_call_volume.append(QualifDailyVolume(day=d, count=by_day.get(d, 0)))
        d += timedelta(days=1)

    return QualifStats(
        period_start=period_start,
        period_end=period_end,
        kpis=kpis,
        by_closer=by_closer,
        by_channel=by_channel,
        top_ko_reasons=top_ko_reasons,
        daily_call_volume=daily_call_volume,
    )
