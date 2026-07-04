"""Tests du harness d'éval — métriques sur un mini-jeu JOUET (aucun réseau).

On ne teste QUE le calcul des métriques (fonctions pures) : pas de scrape, pas de
LLM, pas de vrais comptes. Les paires (label_vérité, bucket_prédit) sont fictives."""
from app.ingestion.eval.metrics import (
    A_CONTACTER,
    bucket_precision,
    confusion_matrix,
    label_recall,
    summarize,
)

ECARTE = "ecarte"


def test_precision_a_contacter_basique():
    # 2 classés a_contacter : 1 vrai opening, 1 established (faux positif) -> 50%.
    pairs = [
        ("opening", A_CONTACTER),
        ("established", A_CONTACTER),
        ("opening", ECARTE),
        ("noise", ECARTE),
    ]
    precision, tp, n_pred = bucket_precision(pairs)
    assert (tp, n_pred) == (1, 2)
    assert precision == 0.5


def test_rappel_opening():
    # 2 opening dans la vérité, 1 seul retrouvé en a_contacter -> 50%.
    pairs = [
        ("opening", A_CONTACTER),
        ("opening", ECARTE),
        ("established", ECARTE),
    ]
    recall, tp, total = label_recall(pairs)
    assert (tp, total) == (1, 2)
    assert recall == 0.5


def test_precision_parfaite_et_confusion():
    pairs = [
        ("opening", A_CONTACTER),
        ("opening", A_CONTACTER),
        ("chain_multisite", ECARTE),
        ("not_venue", ECARTE),
    ]
    report = summarize(pairs)
    assert report.precision_a_contacter == 1.0
    assert report.recall_opening == 1.0
    assert report.false_positives == []
    matrix = confusion_matrix(pairs)
    assert matrix["opening"][A_CONTACTER] == 2
    assert matrix["chain_multisite"][ECARTE] == 1


def test_faux_positifs_listes():
    pairs = [
        ("opening", A_CONTACTER),
        ("chain_multisite", A_CONTACTER),  # faux positif
        ("noise", A_CONTACTER),            # faux positif
    ]
    report = summarize(pairs)
    assert report.precision_a_contacter == 1 / 3
    assert set(report.false_positives) == {
        ("chain_multisite", A_CONTACTER),
        ("noise", A_CONTACTER),
    }


def test_aucun_a_contacter_predit():
    # Rien classé a_contacter -> précision None (pas de division par zéro).
    pairs = [("established", ECARTE), ("noise", ECARTE)]
    report = summarize(pairs)
    assert report.precision_a_contacter is None
    assert report.recall_opening is None  # aucun opening dans la vérité non plus


def test_report_as_dict_serialisable():
    import json
    report = summarize([("opening", A_CONTACTER), ("noise", ECARTE)])
    # doit être sérialisable en JSON sans erreur
    json.dumps(report.as_dict())
