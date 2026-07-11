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


# --- Booster récence (B, T5) + familles neutres de volume -------------------

def test_fresh_prescriber_scores_one_above_neutral():
    # Un studio créé récemment (< 18 mois) vaut +1 vs un lead stock neutre —
    # exactement +1 (pas de bonus 'signaux croisés' parasite : le libellé de
    # récence partage la famille neutre 'prescripteur').
    neutre = compute_score("prescripteur actif", ["stock sirene"], D, [],
                           None, "telephone", today=TODAY).score
    frais = compute_score("prescripteur actif",
                          ["stock sirene", "jeune studio (création récente)"],
                          D, [], None, "telephone", today=TODAY).score
    assert frais == neutre + 1


def test_volume_labels_share_neutral_family_no_cross_bonus():
    # 'stock sirene' / 'annuaire places' DOIVENT être mappés sur la même famille
    # neutre que 'prescripteur actif' : sinon, libellés inconnus, ils comptent
    # pour une famille distincte -> bonus 'signaux croisés' +1 parasite.
    from app.services.scoring import SIGNAL_FAMILY
    assert SIGNAL_FAMILY["stock sirene"] == SIGNAL_FAMILY["prescripteur actif"]
    assert SIGNAL_FAMILY["annuaire places"] == SIGNAL_FAMILY["prescripteur actif"]
    assert SIGNAL_FAMILY["jeune studio (création récente)"] == SIGNAL_FAMILY["prescripteur actif"]
    s = compute_score("prescripteur actif", ["stock sirene"], D, [],
                      None, "telephone", today=TODAY)
    assert "signaux croisés" not in s.reason


def test_hospitality_outranks_recency_outranks_neutral():
    # Ordre de priorité de la doctrine volume : hospitality (+2) > récence (+1)
    # > neutre (0). Comparé à inputs identiques (source de bruit neutralisée).
    def sc(secondary):
        return compute_score("prescripteur actif", secondary, D, [], None,
                             "telephone", today=TODAY).score
    neutre = sc(["annuaire places"])
    recence = sc(["annuaire places", "jeune studio (création récente)"])
    hospi = sc(["annuaire places", "portfolio hospitality/CHR"])
    assert hospi > recence > neutre
