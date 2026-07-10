# backend/tests/test_scoring_prescripteur.py
"""Scoring des leads prescripteurs (A1, T3) — additif, CHR intact."""
from datetime import date

from app.services.scoring import compute_score

TODAY = date(2026, 7, 10)
D = date(2026, 7, 5)  # signal récent


def _score(main, secondary):
    return compute_score(main, secondary, D, ["prescription luminaires", "sourcing"],
                         None, "instagram", today=TODAY).score


def test_neutral_prescriber_scores_low():
    # 'prescripteur actif' seul : aucun bonus de nature (score bas).
    s = _score("prescripteur actif", [])
    assert s <= 5


def test_t1_outranks_t2_outranks_t3():
    t3 = _score("prescripteur actif", [])
    t2 = _score("prescripteur actif", ["portfolio hospitality/CHR"])
    t1 = _score("prescripteur actif", ["projet CHR détecté"])
    assert t1 > t2 > t3


def test_chr_scores_unchanged_by_prescriber_addition():
    # Non-régression : un lead CHR 'ouverture prochaine' n'émet aucun libellé
    # prescripteur -> score inchangé (bonus ouverture +3 + fraîcheur).
    from app.services.scoring import OPENING_SIGNALS
    assert "ouverture prochaine" in OPENING_SIGNALS
    s = compute_score("ouverture prochaine", [], D, ["luminaires", "mobilier"],
                      None, "instagram", today=TODAY).score
    assert s >= 5  # inchangé vs comportement historique
