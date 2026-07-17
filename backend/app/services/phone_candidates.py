"""Numéros candidats « à tester » (chantier multi-numéros).

`Opportunity.phone` reste LA vérité pour les listes d'appel, `tel:`, la
corroboration et la dédup — INCHANGÉ par ce module. `phone_candidates` est une
couche ADDITIVE : des numéros vus par un producteur (site/annuaire/places/
cross-fill) mais qui n'ont pas gagné le principal, conservés « à tester » au
lieu d'être jetés. Module PUR (aucun réseau, aucune session DB) — réutilisé par
les producteurs (`ingestion/`) ET l'endpoint de promotion (`routes/`), pour
éviter trois implémentations divergentes de la même règle.

Cf. docs/plans/2026-07-17-multi-numeros-design.md.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from ..ingestion.enrichment.website_scraper import normalize_phone

# Provenances valides d'un candidat (source d'où le numéro a été vu).
# `ex_principal` = posé UNIQUEMENT par `promote` quand l'ancien principal
# redescend en candidat (on ne connaît pas sa provenance d'origine).
PHONE_CANDIDATE_SOURCES = ["site", "annuaire", "places", "cross_fill", "ex_principal"]

# Cap DUR : au-delà, un lead à > 5 numéros distincts est du bruit.
MAX_PHONE_CANDIDATES = 5


def add_candidate(
    opp,
    number: Optional[str],
    source: str,
    proof_url: Optional[str] = None,
    *,
    today: Optional[date] = None,
) -> bool:
    """Ajoute un numéro candidat à `opp.phone_candidates`, EN PLACE, si (et
    seulement si) il est neuf et utile. Renvoie True s'il a été ajouté.

    Règles (VIDE > FAUX ne s'applique PAS ici : un candidat douteux est
    explicitement « à tester », jamais affiché comme certain) :
      - normalisation `normalize_phone(number)` ; motif implausible -> rejeté
        (on ne stocke pas un candidat qu'on ne sait pas appeler) ;
      - jamais un doublon du PRINCIPAL (`opp.phone`, forme normalisée) ;
      - jamais un doublon entre candidats (comparaison sur `number` normalisé) ;
      - cap DUR à 5 : au-delà, le nouveau est ignoré, l'ordre `first_seen` déjà
        en place est préservé.

    Idempotent : rappeler avec le même `(opp, number)` est un no-op (True la
    1ʳᵉ fois, False ensuite)."""
    normalized = normalize_phone(number)
    if not normalized:
        return False
    if opp.phone and normalize_phone(opp.phone) == normalized:
        return False
    candidates = list(opp.phone_candidates or [])
    if any(c.get("number") == normalized for c in candidates):
        return False
    if len(candidates) >= MAX_PHONE_CANDIDATES:
        return False

    entry = {
        "number": normalized,
        "source": source,
        "first_seen": (today or date.today()).isoformat(),
    }
    if proof_url:
        entry["proof_url"] = proof_url
    candidates.append(entry)
    opp.phone_candidates = candidates
    return True


class PromoteError(Exception):
    """Levée quand le numéro à promouvoir n'est pas (ou plus) un candidat de
    la fiche — appelant (route) responsable de la traduire en 422."""


def promote(opp, number: str, *, today: Optional[date] = None) -> dict:
    """Promotion MANUELLE d'un candidat en principal (geste du closer, tracé
    par l'appelant — cf. §4 du design). En place sur `opp` :

      1. `number` doit normaliser vers un candidat EXISTANT, sinon
         `PromoteError` (l'appelant la traduit en 422) ;
      2. l'ancien `opp.phone` (s'il existe) redescend en candidat
         `source='ex_principal'` (idempotent/dédupliqué via `add_candidate`) ;
      3. le candidat promu QUITTE `phone_candidates` et devient `opp.phone` ;
      4. cap 5 réappliqué (via `add_candidate`).

    Renvoie une COPIE de l'entrée candidate promue (avant le swap) — utile à
    l'appelant pour tracer la provenance (« source site ») dans l'activité de
    promotion, sans avoir à ré-implémenter la recherche."""
    normalized = normalize_phone(number)
    candidates = list(opp.phone_candidates or [])
    match = next((c for c in candidates if c.get("number") == normalized), None)
    if not normalized or match is None:
        raise PromoteError(f"Numéro hors des candidats de la fiche : {number!r}")

    promoted = dict(match)
    old_principal = opp.phone
    opp.phone_candidates = [c for c in candidates if c is not match]
    opp.phone = normalized
    if old_principal:
        add_candidate(opp, old_principal, "ex_principal", today=today)
    return promoted
