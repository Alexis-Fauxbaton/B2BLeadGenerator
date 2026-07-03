"""Cycle de vie d'une fiche — fonctions PURES dérivées (rien de stocké).

Cf. docs/lead-lifecycle-design.md (phase ①). Trois axes distincts qui se
COMBINENT à l'affichage :
- `lifecycle_stage` : où en est le LIEU (pré-ouverture / ouvert récemment /
  établi / fermé).
- `heat` : y a-t-il un MOMENT D'ACHAT actif (récent, dans sa fenêtre) — indépendant
  du stage ("établi mais chaud").
- `freshness` : à quel point notre info est RÉCENTE (dernier événement connu).

Seuils = constantes réglables. Calculé à la volée => jamais désynchronisé.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from .scoring import (
    OPENING_SIGNALS,
    RECRUITMENT_SIGNALS,
    RENOVATION_SIGNALS,
    TAKEOVER_SIGNALS,
)

# Signaux "moment d'achat" (poids > 0) — pilotent la chaleur.
BUYING_SIGNALS = OPENING_SIGNALS | TAKEOVER_SIGNALS | RENOVATION_SIGNALS | RECRUITMENT_SIGNALS

# --- Seuils (à caler) --------------------------------------------------------
ESTABLISHED_REVIEWS = 200   # >= => "établi" (fenêtre passée)
OPENED_REVIEWS = 20         # <= (et > 0) => a déjà des avis => "ouvert récemment"
PREOPENING_MAX_AGE_DAYS = 45  # signal d'ouverture récent => "pré-ouverture"

HOT_MAX_AGE_DAYS = 60       # moment d'achat <= 60 j => chaud
WARM_MAX_AGE_DAYS = 120     # <= 120 j => tiède ; au-delà => froid

FRESH_MAX_DAYS = 30         # dernier événement <= 30 j => fraîche
REFRESH_MAX_DAYS = 90       # <= 90 j => à rafraîchir ; au-delà => périmée


def lifecycle_stage(
    main_signal: str,
    review_count: Optional[int],
    detection_date: date,
    today: Optional[date] = None,
    closed: bool = False,
) -> str:
    """pré-ouverture | ouvert récemment | établi | fermé."""
    today = today or date.today()
    if closed:
        return "fermé"
    if review_count is not None:
        if review_count >= ESTABLISHED_REVIEWS:
            return "établi"
        if review_count > 0:
            return "ouvert récemment"  # a des avis mais peu
    age = (today - detection_date).days
    if main_signal in OPENING_SIGNALS and age <= PREOPENING_MAX_AGE_DAYS:
        return "pré-ouverture"
    return "ouvert récemment"


def heat(
    main_signal: str,
    detection_date: date,
    today: Optional[date] = None,
) -> str:
    """chaud | tiède | froid — un moment d'achat est-il encore dans sa fenêtre ?
    Indépendant du stage (un lieu établi peut être chaud via recrutement/repri.)."""
    today = today or date.today()
    if main_signal not in BUYING_SIGNALS:
        return "froid"
    age = (today - detection_date).days
    if age <= HOT_MAX_AGE_DAYS:
        return "chaud"
    if age <= WARM_MAX_AGE_DAYS:
        return "tiède"
    return "froid"


def freshness(
    last_event_date: Optional[date],
    today: Optional[date] = None,
) -> str:
    """fraîche | à rafraîchir | périmée — récence de notre dernière info."""
    today = today or date.today()
    if last_event_date is None:
        return "à rafraîchir"
    age = (today - last_event_date).days
    if age <= FRESH_MAX_DAYS:
        return "fraîche"
    if age <= REFRESH_MAX_DAYS:
        return "à rafraîchir"
    return "périmée"
