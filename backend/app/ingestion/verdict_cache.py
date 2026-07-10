"""Cache de verdicts du funnel Insta v2 (brique 3 du pivot inventaire).

Métier PUR, session SQLModel injectée (aucun engine global) : le cache évite de
re-scraper/re-juger un handle déjà tranché tant que sa fenêtre de revisite n'est
pas expirée. Fenêtres : not_venue +12 mois, established/chain_multisite +6 mois,
noise/unknown +2 mois, opening_soon/just_opened/renovation jamais mis en sommeil
(segments chauds / watchlist active, brique 4).

INVALIDATION PAR EMPREINTE (profile_hash) — ATTENTION : elle n'est exercée que
lorsqu'un profil est PASSÉ à should_rejudge. Or le seul appelant en production
(pipeline.run_instagram) l'appelle AVANT le scrape avec `profile=None` (on ne
peut pas comparer une empreinte qu'on n'a pas encore scrapée). Donc AUJOURD'HUI
seule la fenêtre temporelle pilote la revisite ; le contrôle d'empreinte est
RÉSERVÉ à la revisite périodique légère de la brique 4 (qui, elle, aura le
profil en main). Le hash est écrit à chaque upsert pour que ce chemin futur
existe, mais ne déclenche aucune invalidation dans le flux hashtag actuel.
Cf. docs/inventory-pivot-design.md (« Cache de verdicts »).
"""
from __future__ import annotations

import calendar
import hashlib
from datetime import date, datetime
from typing import Any, Dict, Optional

from sqlmodel import Session, select

from ..models import HandleVerdict

# Fenêtres de revisite en mois par verdict. Absents de ce dict = jamais mis en
# sommeil (opening_soon/just_opened -> revisit_after None -> toujours re-jugés).
REVISIT_MONTHS = {
    "not_venue": 12,
    "established": 6,
    "chain_multisite": 6,
    "noise": 2,
    "unknown": 2,
    # Population architectes (A1) : hors_cible/compte_perso longtemps en sommeil ;
    # studio_dormant 6 mois ; studio_actif re-visité souvent (2 mois) pour capter
    # le booster « nouveau projet » (tier T1).
    "hors_cible": 12,
    "compte_perso": 12,
    "studio_dormant": 6,
    "studio_actif": 2,
}
NEVER_CACHED = ("opening_soon", "just_opened", "renovation")


def profile_hash(profile: Dict[str, Any]) -> str:
    """sha1 de (biography + postsCount) : empreinte de contenu du profil. Un
    changement (nouvelle bio, nouveaux posts) invalide le verdict en cache."""
    bio = (profile or {}).get("biography") or ""
    posts = (profile or {}).get("postsCount")
    return hashlib.sha1(f"{bio}|{posts}".encode("utf-8")).hexdigest()


def _add_months(d: date, months: int) -> date:
    """d + N mois, en bornant le jour au dernier jour du mois cible (fin de mois
    robuste). Arithmétique de dates EN CODE (jamais déléguée à un LLM)."""
    total = d.month - 1 + months
    year = d.year + total // 12
    month = total % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def revisit_after(verdict: str, today: date) -> Optional[date]:
    """Date de re-jugement pour un verdict, ou None (jamais mis en sommeil)."""
    if verdict in NEVER_CACHED:
        return None
    return _add_months(today, REVISIT_MONTHS.get(verdict, 2))


def get(session: Session, handle: str) -> Optional[HandleVerdict]:
    return session.exec(
        select(HandleVerdict).where(HandleVerdict.handle == handle)
    ).first()


def should_rejudge(
    session: Session, handle: str,
    profile: Optional[Dict[str, Any]] = None, today: Optional[date] = None,
) -> bool:
    """True s'il faut (re)scraper/re-juger ce handle. En production, appelé AVANT
    le scrape avec `profile=None` -> décision sur la SEULE fenêtre temporelle. Le
    contrôle d'empreinte `profile_hash` n'a lieu que si un profil est fourni, ce
    qui n'arrive PAS dans le flux hashtag actuel (réservé à la revisite périodique
    de la brique 4). NB : tant que ce chemin n'existe pas, un not_venue mal jugé
    reste verrouillé toute sa fenêtre (12 mois) sans échappatoire par changement
    de profil — à garder en tête au moment de câbler la brique 4."""
    today = today or date.today()
    v = get(session, handle)
    if v is None:
        return True
    if profile is not None and profile_hash(profile) != v.profile_hash:
        return True
    if v.revisit_after is None:
        return True  # opening_soon/just_opened : jamais mis en sommeil
    return today >= v.revisit_after


def upsert(
    session: Session, handle: str, verdict: str,
    confidence: Optional[str], profile: Dict[str, Any],
    today: Optional[date] = None,
) -> HandleVerdict:
    """Écrit/actualise le verdict d'un handle (une seule ligne par handle)."""
    today = today or date.today()
    v = get(session, handle)
    now = datetime.utcnow()
    h = profile_hash(profile or {})
    ra = revisit_after(verdict, today)
    if v is None:
        v = HandleVerdict(handle=handle, verdict=verdict, confidence=confidence,
                          judged_at=now, revisit_after=ra, profile_hash=h)
    else:
        v.verdict = verdict
        v.confidence = confidence
        v.judged_at = now
        v.revisit_after = ra
        v.profile_hash = h
    session.add(v)
    session.flush()
    return v
