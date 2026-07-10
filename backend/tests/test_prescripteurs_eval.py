# backend/tests/test_prescripteurs_eval.py
"""Éval prescripteurs (A1, T5) — métriques PURES + gardes offline. Pas de LLM."""
import csv
from datetime import date
from pathlib import Path

from app.ingestion.eval.prescripteurs_metrics import (
    studio_actif_precision, hors_cible_in_tiers,
)
from app.ingestion.instagram import classify_prescripteurs

ROOT = Path(__file__).resolve().parents[1] / "app" / "ingestion" / "eval"
CSV = ROOT / "architectes_groundtruth.csv"
SNAP = ROOT / "snapshots_architectes"
TODAY = date(2026, 7, 10)


def test_groundtruth_csv_seeded():
    rows = list(csv.DictReader(CSV.open(encoding="utf-8")))
    handles = {r["handle"] for r in rows}
    # Seed sonde : au moins les 4 hors_cible + le compte_perso + le dormant.
    for h in ("atelierlesimple", "cotefauteuils", "endora.studio3d", "habiteretgrandir",
              "divnaanni", "helene.gombert", "atelier_jdp"):
        assert h in handles, f"{h} absent du CSV"
    labels = {r["label"] for r in rows}
    assert labels <= {"studio_actif", "studio_dormant", "compte_perso", "hors_cible"}


def test_studio_actif_precision_metric():
    pairs = [("studio_actif", "studio_actif"), ("compte_perso", "studio_actif"),
             ("studio_actif", "studio_dormant")]
    prec, tp, n = studio_actif_precision(pairs)
    assert (tp, n) == (1, 2) and abs(prec - 0.5) < 1e-9


def test_hors_cible_in_tiers_detects_violation():
    rows = [{"handle": "a", "true_label": "hors_cible", "tier": "T2"},
            {"handle": "b", "true_label": "studio_actif", "tier": "T1"}]
    assert hors_cible_in_tiers(rows) == ["a"]
    assert hors_cible_in_tiers([{"handle": "b", "true_label": "studio_actif", "tier": "T1"}]) == []


def test_guards_catch_all_sonde_hors_cible_offline():
    # Sans LLM (client=None), les gardes déterministes doivent classer hors_cible
    # les 4 comptes hors_cible de la sonde (grounded).
    import json
    cands, profs = [], {}
    for h in ("atelierlesimple", "cotefauteuils", "endora.studio3d", "habiteretgrandir"):
        p = SNAP / f"{h}.json"
        if not p.exists():
            continue
        profs[h.lower()] = json.loads(p.read_text(encoding="utf-8"))
        cands.append({"handle": h, "name": h, "city": "", "type": "architecte d'intérieur",
                      "caption": "", "population": "architecte"})
    assert cands, "snapshots_architectes manquants"
    out = classify_prescripteurs(cands, profs, client=None, match_fn=None, today=TODAY)
    for c in out:
        assert c["label"] == "hors_cible", f'{c["handle"]} -> {c["label"]}'
