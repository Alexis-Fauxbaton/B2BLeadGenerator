"""Harness d'éval de la classification des leads Instagram (CLI).

Tourne sur des SNAPSHOTS figés (`snapshots/<handle>.json` = sortie brute du
profile scraper Apify) -> reproductible, indépendant d'un scrape live. Mesure
l'état ACTUEL du pipeline ; ne règle AUCUN seuil, n'ajoute AUCUNE règle bucket.

  python -m app.ingestion.eval.run               # éval sur les snapshots présents
  python -m app.ingestion.eval.run --snapshot    # (re)peuple les snapshots (Apify)
  python -m app.ingestion.eval.run --strict      # exclut confidence=low
  python -m app.ingestion.eval.run --json out.json

Classif sous test = le pipeline actuel : `profile_enrich` (garde-fous
déterministes + juge LLM unitaire) projeté dans l'espace des buckets —
gardé -> `a_contacter`, écarté -> `ecarte`. La couche buckets fine viendra
APRÈS, réglée grâce à ce harness.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .metrics import A_CONTACTER, summarize

ROOT = Path(__file__).resolve().parent
CSV_PATH = ROOT / "instagram_groundtruth.csv"
SNAP_DIR = ROOT / "snapshots"
RESULT_PATH = ROOT / "eval_result.json"  # cache du dernier résultat détaillé

ECARTE = "ecarte"

# Bucket cible par label vérité (cf. README). Sert à l'affichage : le statut
# "véridique" attendu. La prédiction actuelle est binaire (a_contacter/ecarte).
TRUE_BUCKET = {
    "opening": "a_contacter",
    "just_opened": "a_surveiller",
    "established": "ecarte",
    "chain_multisite": "ecarte",
    "not_venue": "ecarte",
    "noise": "a_reverifier",
}


def load_groundtruth() -> List[dict]:
    with CSV_PATH.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def snapshot_path(handle: str) -> Path:
    return SNAP_DIR / f"{handle}.json"


def populate_snapshots(rows: List[dict]) -> Tuple[int, List[str]]:
    """Scrape le profil de chaque handle et fige le JSON. Fail-soft : sans token
    (ou profil introuvable), on saute le handle sans crasher. -> (ok, sautés)."""
    from ..instagram import scrape_profiles

    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    ok, skipped = 0, []
    for row in rows:
        handle = row["handle"].strip()
        profiles = scrape_profiles([handle])
        prof = profiles.get(handle.lower())
        if not prof:
            skipped.append(handle)
            continue
        snapshot_path(handle).write_text(
            json.dumps(prof, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        ok += 1
    return ok, skipped


def load_snapshot(handle: str) -> Optional[dict]:
    p = snapshot_path(handle)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def classify(snapshots: Dict[str, dict]) -> Dict[str, str]:
    """Projette le verdict du pipeline actuel dans l'espace des buckets.
    `snapshots` = {handle: profil figé}. -> {handle: 'a_contacter'|'ecarte'}.

    On passe tous les comptes par `profile_enrich` (profils injectés) : ceux qui
    survivent = `a_contacter`, les écartés = `ecarte`."""
    from ..instagram import profile_enrich

    candidates = [
        {"handle": h, "name": (snap.get("fullName") or h)}
        for h, snap in snapshots.items()
    ]
    injected = {h.lower(): snap for h, snap in snapshots.items()}
    kept = profile_enrich([dict(c) for c in candidates], profiles=injected)
    kept_handles = {c["handle"] for c in kept}
    return {h: (A_CONTACTER if h in kept_handles else ECARTE) for h in snapshots}


def run_eval(strict: bool = False) -> dict:
    rows = load_groundtruth()
    snapshots: Dict[str, dict] = {}
    missing: List[str] = []
    excluded_low: List[str] = []
    for row in rows:
        handle = row["handle"].strip()
        if strict and row.get("confidence", "").strip() == "low":
            excluded_low.append(handle)
            continue
        snap = load_snapshot(handle)
        if snap is None:
            missing.append(handle)
            continue
        snapshots[handle] = snap

    predictions = classify(snapshots) if snapshots else {}
    label_by_handle = {r["handle"].strip(): r["label"].strip() for r in rows}
    pairs = [(label_by_handle[h], predictions[h]) for h in snapshots]

    report = summarize(pairs)
    return {
        "report": report,
        "missing_snapshots": missing,
        "excluded_low_confidence": excluded_low,
        "predictions": predictions,
        "label_by_handle": label_by_handle,
    }


def detailed_result(strict: bool = False) -> dict:
    """Résultat détaillé par handle (pour l'API / l'inspection dans l'app) :
    statut véridique (label + bucket cible) vs statut inféré (bucket prédit),
    + confiance/provenance/justification + lien Instagram."""
    res = run_eval(strict=strict)
    r = res["report"]
    preds = res["predictions"]
    missing = set(res["missing_snapshots"])
    rows: List[dict] = []
    for row in load_groundtruth():
        h = row["handle"].strip()
        label = row["label"].strip()
        pred = preds.get(h)  # 'a_contacter' | 'ecarte' | None (snapshot manquant/exclu)
        rows.append({
            "handle": h,
            "name": row.get("name", "").strip(),
            "true_label": label,
            "true_bucket": TRUE_BUCKET.get(label, "?"),
            "predicted_bucket": pred,
            "confidence": row.get("confidence", "").strip(),
            "provenance": row.get("provenance", "").strip(),
            "rationale": row.get("rationale", "").strip(),
            "ig_url": f"https://instagram.com/{h}",
            "false_positive": pred == A_CONTACTER and label != "opening",
            "missed_opening": label == "opening" and pred == ECARTE,
            "has_snapshot": h not in missing,
        })
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "precision_a_contacter": r.precision_a_contacter,
        "recall_opening": r.recall_opening,
        "n": r.n,
        "n_a_contacter": r.n_a_contacter,
        "tp_opening": r.tp_opening,
        "n_opening": r.n_opening,
        "confusion": r.confusion,
        "rows": rows,
    }


def cached_result(refresh: bool = False) -> dict:
    """Sert le dernier résultat détaillé depuis le cache fichier (évite de relancer
    le LLM à chaque affichage). `refresh=True` recalcule et réécrit le cache."""
    if not refresh and RESULT_PATH.exists():
        try:
            return json.loads(RESULT_PATH.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            pass
    result = detailed_result()
    try:
        RESULT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
    return result


def _fmt_pct(x: Optional[float]) -> str:
    return "n/a" if x is None else f"{x * 100:.0f}%"


def print_report(result: dict) -> None:
    r = result["report"]
    print("=" * 60)
    print("EVAL — classification des leads Instagram (état actuel)")
    print("=" * 60)
    print(f"Comptes évalués      : {r.n}")
    if result["missing_snapshots"]:
        print(f"Snapshots manquants  : {len(result['missing_snapshots'])} "
              f"({', '.join(result['missing_snapshots'])})  -> lancer --snapshot")
    if result["excluded_low_confidence"]:
        print(f"Exclus (conf=low)    : {len(result['excluded_low_confidence'])}")
    print()
    print(f"** PRÉCISION a_contacter : {_fmt_pct(r.precision_a_contacter)} **"
          f"   ({r.tp_opening} opening / {r.n_a_contacter} classés a_contacter)")
    print(f"   Rappel des opening    : {_fmt_pct(r.recall_opening)}"
          f"   ({r.tp_opening} retrouvés / {r.n_opening} opening)")
    print()
    print("Matrice de confusion (label vérité -> bucket prédit) :")
    buckets = [A_CONTACTER, ECARTE]
    print(f"  {'label':<16} " + " ".join(f"{b:>12}" for b in buckets))
    for label in sorted(r.confusion):
        row = r.confusion[label]
        print(f"  {label:<16} " + " ".join(f"{row.get(b, 0):>12}" for b in buckets))
    if r.false_positives:
        print()
        print("Faux positifs (classés a_contacter mais label != opening) :")
        fp_handles = [
            h for h in result["predictions"]
            if result["predictions"][h] == A_CONTACTER
            and result["label_by_handle"].get(h) != "opening"
        ]
        for h in fp_handles:
            print(f"  - {h}  (vérité: {result['label_by_handle'].get(h)})")
    print("=" * 60)


def main() -> None:
    # Charge les clés (APIFY_TOKEN / OPENAI_API_KEY) depuis backend/.env, y compris
    # en exécution `python -m ...` où le cwd/chemin d'appel varie.
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT.parents[2] / ".env")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="Éval classification leads Instagram")
    parser.add_argument("--snapshot", action="store_true",
                        help="(re)peuple les snapshots de profils depuis le CSV (Apify)")
    parser.add_argument("--strict", action="store_true",
                        help="exclut les lignes confidence=low du calcul")
    parser.add_argument("--json", metavar="PATH",
                        help="écrit aussi le rapport en JSON")
    args = parser.parse_args()

    rows = load_groundtruth()

    if args.snapshot:
        from ..instagram import has_token
        if not has_token():
            print("APIFY_TOKEN absent -> impossible de peupler les snapshots.")
            return
        ok, skipped = populate_snapshots(rows)
        print(f"Snapshots écrits : {ok} | sautés (profil introuvable) : {len(skipped)}")
        if skipped:
            print("  sautés :", ", ".join(skipped))
        return

    result = run_eval(strict=args.strict)
    print_report(result)
    if args.json:
        payload = {
            **result["report"].as_dict(),
            "missing_snapshots": result["missing_snapshots"],
            "excluded_low_confidence": result["excluded_low_confidence"],
        }
        Path(args.json).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Rapport JSON écrit : {args.json}")


if __name__ == "__main__":
    main()
