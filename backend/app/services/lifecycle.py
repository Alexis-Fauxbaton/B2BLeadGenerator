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
ESTABLISHED_AGE_DAYS = 730  # local dont l'origine (reprise) date de >= 2 ans => "établi"

HOT_MAX_AGE_DAYS = 60       # moment d'achat <= 60 j => chaud
WARM_MAX_AGE_DAYS = 120     # <= 120 j => tiède ; au-delà => froid

FRESH_MAX_DAYS = 30         # dernier événement <= 30 j => fraîche
REFRESH_MAX_DAYS = 90       # <= 90 j => à rafraîchir ; au-delà => périmée


ESTABLISHED_LABELS = ("established", "chain_multisite")


def lifecycle_stage(
    main_signal: str,
    review_count: Optional[int],
    detection_date: date,
    today: Optional[date] = None,
    closed: bool = False,
    activity_start_date: Optional[date] = None,
    venue_origin_date: Optional[date] = None,
    lifecycle_label: Optional[str] = None,
) -> str:
    """pré-ouverture | ouvert récemment | établi | fermé.

    `venue_origin_date` = date d'origine du LOCAL (via le précédent exploitant
    d'une reprise) : un vieux local repris est un lieu ÉTABLI (le stage décrit le
    lieu ; la chaleur, elle, reste chaude via le signal reprise récent -> cas
    "établi mais chaud").

    `lifecycle_label` = verdict de cycle de vie PERSISTÉ (funnel Insta brique 3bis).
    Un label established/chain_multisite est un jugement de classification qui fait
    foi : il force le stage "établi" AVANT le repli heuristique, pour que l'axe
    persisté (lifecycle_label) et l'axe dérivé (lifecycle_stage) ne se contredisent
    pas (une fiche Insta « en base » n'a ni avis, ni origine, ni date d'activité :
    sans cela elle retomberait à tort sur "ouvert récemment")."""
    today = today or date.today()
    if closed:
        return "fermé"
    # Établi = le LIEU est ancien : soit beaucoup d'avis, soit une origine
    # (reprise) datant de >= 2 ans.
    if review_count is not None and review_count >= ESTABLISHED_REVIEWS:
        return "établi"
    if venue_origin_date is not None and (today - venue_origin_date).days >= ESTABLISHED_AGE_DAYS:
        return "établi"
    if review_count is not None and review_count > 0:
        return "ouvert récemment"  # a des avis => déjà ouvert
    # Discriminant FIABLE (registre) : date de début d'activité (du repreneur/
    # nouvel exploitant). Future => pas encore ouvert ; passée => déjà ouvert.
    if activity_start_date is not None:
        return "pré-ouverture" if activity_start_date > today else "ouvert récemment"
    # Label de cycle de vie persisté : un établi/chaîne fait foi avant tout repli
    # heuristique (cohérence lifecycle_label <-> lifecycle_stage).
    if lifecycle_label in ESTABLISHED_LABELS:
        return "établi"
    # Repli heuristique (pas de date) : signal d'ouverture récent => pré-ouverture.
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
