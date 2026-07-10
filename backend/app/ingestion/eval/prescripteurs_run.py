"""Harness d'éval de la classification PRESCRIPTEURS (A1) — CLI.

Tourne sur des snapshots figés (snapshots_architectes/<handle>.json). Reproductible,
SÉPARÉ de l'éval CHR (qui reste intacte). Le LLM (juge prescripteur) n'est appelé
QUE si OPENAI_API_KEY est présent — c'est le gate d'acceptation (T6).

  python -m app.ingestion.eval.prescripteurs_run
  python -m app.ingestion.eval.prescripteurs_run --json out.json
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

from ..instagram import classify_prescripteurs
from .prescripteurs_metrics import (
    LABEL_ORDER, hors_cible_in_tiers, label_confusion, studio_actif_precision,
)

ROOT = Path(__file__).resolve().parent
CSV_PATH = ROOT / "architectes_groundtruth.csv"
SNAP_DIR = ROOT / "snapshots_architectes"

GATE_STUDIO_PRECISION = 0.70  # précision studio_actif >= 70 %


def load_groundtruth() -> List[dict]:
    with CSV_PATH.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_snapshot(handle: str) -> Optional[dict]:
    p = SNAP_DIR / f"{handle}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def run_prescripteurs_eval(strict: bool = False, today: Optional[date] = None) -> dict:
    today = today or date.today()
    rows = load_groundtruth()
    snapshots: Dict[str, dict] = {}
    missing: List[str] = []
    for row in rows:
        h = row["handle"].strip()
        snap = load_snapshot(h)
        if snap is None:
            missing.append(h)
            continue
        snapshots[h] = snap

    cands = [{"handle": h, "name": (snap.get("fullName") or h), "city": "",
              "type": "architecte d'intérieur", "caption": "", "population": "architecte"}
             for h, snap in snapshots.items()]
    injected = {h.lower(): snap for h, snap in snapshots.items()}
    labeled = classify_prescripteurs([dict(c) for c in cands], injected,
                                     match_fn=None, today=today)
    pred_by_handle = {c["handle"]: c for c in labeled}
    truth_by_handle = {r["handle"].strip(): r["label"].strip() for r in rows}

    pairs = [(truth_by_handle[h], pred_by_handle[h]["label"]) for h in snapshots]
    prec, tp, n = studio_actif_precision(pairs)
    detail_rows = [{"handle": h, "true_label": truth_by_handle[h],
                    "predicted_label": pred_by_handle[h]["label"],
                    "tier": pred_by_handle[h].get("tier")} for h in snapshots]
    violations = hors_cible_in_tiers(detail_rows)

    gate_precision = prec is not None and prec >= GATE_STUDIO_PRECISION
    gate_tiers = len(violations) == 0
    return {
        "n": len(snapshots), "missing": missing,
        "studio_actif_precision": prec, "studio_actif_tp": tp, "studio_actif_n": n,
        "hors_cible_in_tiers": violations,
        "confusion": label_confusion(pairs),
        "gate_studio_precision": gate_precision,
        "gate_zero_hors_cible_in_tiers": gate_tiers,
        "gates_pass": gate_precision and gate_tiers,
        "rows": detail_rows,
    }


def print_report(res: dict) -> None:
    print("=" * 60)
    print("ÉVAL — classification prescripteurs (architectes, A1)")
    print("=" * 60)
    print(f"Comptes évalués : {res['n']}")
    if res["missing"]:
        print(f"Snapshots manquants : {len(res['missing'])} ({', '.join(res['missing'])})")
    p = res["studio_actif_precision"]
    pct = "n/a" if p is None else f"{p*100:.0f}%"
    print(f"** PRÉCISION studio_actif : {pct} ** ({res['studio_actif_tp']}/{res['studio_actif_n']})")
    print(f"hors_cible en T1/T2 (doit être vide) : {res['hors_cible_in_tiers']}")
    print("Matrice (vérité -> prédit) :")
    print(f"  {'vérité':<16} " + " ".join(f"{c[:9]:>10}" for c in LABEL_ORDER))
    for t in LABEL_ORDER:
        if t in res["confusion"]:
            r = res["confusion"][t]
            print(f"  {t:<16} " + " ".join(f"{r.get(c, 0):>10}" for c in LABEL_ORDER))
    ok = "OK" if res["gates_pass"] else "ÉCHEC"
    print(f"GATES : précision studio_actif>=70% = {res['gate_studio_precision']} | "
          f"0 hors_cible en T1/T2 = {res['gate_zero_hors_cible_in_tiers']} -> {ok}")
    print("=" * 60)


def main() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT.parents[2] / ".env")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="Éval classification prescripteurs (archi)")
    parser.add_argument("--json", metavar="PATH")
    args = parser.parse_args()
    res = run_prescripteurs_eval()
    print_report(res)
    if args.json:
        Path(args.json).write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    import sys
    sys.exit(0 if res["gates_pass"] else 1)


if __name__ == "__main__":
    main()
