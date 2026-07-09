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


def test_label_confusion_matrix():
    from app.ingestion.eval.metrics import label_confusion
    pairs = [
        ("opening_soon", "opening_soon"),
        ("opening_soon", "unknown"),
        ("chain_multisite", "chain_multisite"),
        ("not_venue", "established"),
    ]
    m = label_confusion(pairs)
    assert m["opening_soon"]["opening_soon"] == 1
    assert m["opening_soon"]["unknown"] == 1
    assert m["chain_multisite"]["chain_multisite"] == 1
    assert m["not_venue"]["established"] == 1


def test_hot_precision():
    from app.ingestion.eval.metrics import hot_precision
    pairs = [
        ("opening_soon", "opening_soon"),      # TP (vérité chaude, prédit chaud)
        ("just_opened", "just_opened"),        # TP (just_opened prédit sur vrai just_opened)
        ("established", "opening_soon"),       # FP (établi prédit chaud)
        ("not_venue", "noise"),                # hors segment chaud
        ("chain_multisite", "chain_multisite"),  # hors segment chaud
        ("opening_soon", "unknown"),           # hors segment chaud (unknown pas chaud)
    ]
    prec, tp, n = hot_precision(pairs)
    assert (tp, n) == (2, 3)
    assert abs(prec - 2 / 3) < 1e-9
    # Aucun prédit chaud -> None.
    assert hot_precision([("established", "established")]) == (None, 0, 0)


def test_is_disagreement_v2bis_mapping():
    from app.ingestion.eval.run import is_disagreement
    # Vérité 'opening' (bucket a_contacter) vs prédiction 'established' (en_base) -> désaccord.
    assert is_disagreement("opening", "established") is True
    # Vérité 'opening' vs prédiction 'opening_soon' (a_contacter) -> accord.
    assert is_disagreement("opening", "opening_soon") is False
    # Vérité 'established' (en_base) vs prédiction 'unknown' (en_base) -> accord.
    assert is_disagreement("established", "unknown") is False
    # Vérité 'noise' (ecarte) vs prédiction 'unknown' (en_base) -> désaccord.
    assert is_disagreement("noise", "unknown") is True
    # Pas de prédiction cachée (None) ou label inconnu -> jamais de désaccord affiché.
    assert is_disagreement("opening", None) is False
    assert is_disagreement("opening", "???") is False


def test_groundtruth_asof_filters_by_date(monkeypatch):
    import app.ingestion.eval.run as run
    fake = [
        {"handle": "a", "name": "A", "label": "opening", "confidence": "high",
         "rationale": "r-a", "annotated_at": "2026-07-04"},
        {"handle": "b", "name": "B", "label": "established", "confidence": "low",
         "rationale": "r-b", "annotated_at": "2026-07-08"},
    ]
    monkeypatch.setattr(run, "load_groundtruth", lambda: [dict(r) for r in fake])
    monkeypatch.setattr(run, "_cached_predictions", lambda: {})
    # as_of à la 1re annotation -> seule 'a' visible, as_of effectif = celui demandé.
    res = run.groundtruth_asof(as_of="2026-07-04")
    assert [r["handle"] for r in res["rows"]] == ["a"]
    assert res["total"] == 1 and res["as_of"] == "2026-07-04"
    # Défaut (toutes) -> as_of effectif = date d'annotation la plus récente.
    res_all = run.groundtruth_asof()
    assert res_all["total"] == 2 and res_all["as_of"] == "2026-07-08"
    assert {r["handle"] for r in res_all["rows"]} == {"a", "b"}


def test_groundtruth_asof_fail_soft_without_cache(monkeypatch, tmp_path):
    import app.ingestion.eval.run as run
    fake = [{"handle": "a", "name": "A", "label": "opening", "confidence": "high",
             "rationale": "r", "annotated_at": "2026-07-04"}]
    monkeypatch.setattr(run, "load_groundtruth", lambda: [dict(r) for r in fake])
    # Cache d'éval ABSENT -> predicted None, disagreement False, aucun crash, aucun LLM.
    monkeypatch.setattr(run, "RESULT_PATH", tmp_path / "absent.json")
    res = run.groundtruth_asof(as_of="2026-07-04")
    assert res["rows"][0]["predicted"] is None
    assert res["rows"][0]["disagreement"] is False


def test_groundtruth_csv_backfilled_readable():
    # Le CSV réel porte la colonne annotated_at, remplie sur TOUTES les lignes,
    # et le backfill d'origine vaut 2026-07-04 (session d'annotation initiale).
    from app.ingestion.eval.run import load_groundtruth
    rows = load_groundtruth()
    assert rows and all(r.get("annotated_at", "").strip() for r in rows)
    assert any(r["annotated_at"].strip() == "2026-07-04" for r in rows)
