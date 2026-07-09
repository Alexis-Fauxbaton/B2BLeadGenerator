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

# Labels prédits qui tombent dans le bucket "à contacter" (projection binaire).
FRESH_LABELS = {"opening_soon", "just_opened", "unknown"}
# Mapping du label VÉRITÉ (CSV) vers l'espace des labels prédits.
TRUTH_LABEL_MAP = {"opening": "opening_soon"}
# Gates durs d'acceptation.
GATE_RECALL_OPENING = 1.0
# Plancher de précision a_contacter (métrique de continuité, publiée mais NON
# bloquante depuis la brique 3bis : le gate honnête est la précision du segment
# chaud, cf. GATE_HOT_PRECISION).
GATE_MIN_PRECISION = 0.33
# Gate honnête d'acceptation (brique 3bis) : précision du segment chaud
# (opening_soon/just_opened prédits) >= 60 %.
GATE_HOT_PRECISION = 0.60

# Bucket cible par label vérité (brique 3bis) : establi/chaîne = « en_base »
# (lead créé, segment froid) ; not_venue/noise = « ecarte » (pas de lead) ;
# opening = « a_contacter » ; just_opened = « a_surveiller ».
TRUE_BUCKET = {
    "opening": "a_contacter",
    "just_opened": "a_surveiller",
    "established": "en_base",
    "chain_multisite": "en_base",
    "not_venue": "ecarte",
    "noise": "ecarte",
}

# Bucket cible par label PRÉDIT (miroir de TRUE_BUCKET, même espace de buckets
# v2bis). Sert à comparer vérité vs prédiction dans le « jeu de preuve ».
# unknown -> en_base (lead neutre « en base », cf. routage brique 3bis).
PRED_BUCKET = {
    "opening_soon": "a_contacter",
    "just_opened": "a_surveiller",
    "established": "en_base",
    "chain_multisite": "en_base",
    "unknown": "en_base",
    "not_venue": "ecarte",
    "noise": "ecarte",
}


def is_disagreement(true_label: str, predicted_label: Optional[str]) -> bool:
    """Désaccord = la prédiction tombe dans un bucket v2bis INCOMPATIBLE avec le
    bucket cible du label vérité (TRUE_BUCKET vs PRED_BUCKET, même espace).
    None (pas de prédiction cachée) ou label inconnu -> False (on n'affiche pas de
    désaccord contre une prédiction absente)."""
    if not predicted_label:
        return False
    tb = TRUE_BUCKET.get(true_label)
    pb = PRED_BUCKET.get(predicted_label)
    if tb is None or pb is None:
        return False
    return tb != pb


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
    """Label de cycle de vie prédit par le funnel v2 pour chaque handle.
    `snapshots` = {handle: profil figé}. -> {handle: label}."""
    from ..instagram import classify_profiles

    candidates = [
        {"handle": h, "name": (snap.get("fullName") or h), "city": "", "type": "restaurant"}
        for h, snap in snapshots.items()
    ]
    injected = {h.lower(): snap for h, snap in snapshots.items()}
    labeled = classify_profiles([dict(c) for c in candidates], injected, match_fn=None)
    return {c["handle"]: c["label"] for c in labeled}


def run_eval(strict: bool = False) -> dict:
    from .metrics import label_confusion, hot_precision

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

    predicted_labels = classify(snapshots) if snapshots else {}
    label_by_handle = {r["handle"].strip(): r["label"].strip() for r in rows}

    # Projection binaire (métrique historique : precision a_contacter / recall opening).
    buckets = {h: (A_CONTACTER if predicted_labels[h] in FRESH_LABELS else ECARTE)
               for h in snapshots}
    pairs = [(label_by_handle[h], buckets[h]) for h in snapshots]
    report = summarize(pairs)

    # Matrice de confusion par LABEL (label vérité mappé × label prédit).
    label_pairs = [
        (TRUTH_LABEL_MAP.get(label_by_handle[h], label_by_handle[h]), predicted_labels[h])
        for h in snapshots
    ]
    labels_matrix = label_confusion(label_pairs)
    hot_prec, hot_tp, hot_n = hot_precision(label_pairs)

    gate_recall = report.recall_opening is not None and report.recall_opening >= GATE_RECALL_OPENING
    gate_precision = report.precision_a_contacter is not None and report.precision_a_contacter >= GATE_MIN_PRECISION
    gate_hot = hot_prec is not None and hot_prec >= GATE_HOT_PRECISION
    return {
        "report": report,
        "missing_snapshots": missing,
        "excluded_low_confidence": excluded_low,
        "predictions": buckets,
        "predicted_labels": predicted_labels,
        "label_by_handle": label_by_handle,
        "labels_matrix": labels_matrix,
        "hot_precision": hot_prec,
        "hot_tp": hot_tp,
        "hot_n": hot_n,
        "gate_recall_opening": gate_recall,
        "gate_precision": gate_precision,
        "gate_hot_precision": gate_hot,
        # ACCEPTATION brique 3bis : rappel opening 4/4 ET précision segment chaud >= 60 %.
        "gates_pass": gate_recall and gate_hot,
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
            "predicted_label": res["predicted_labels"].get(h),
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


def _cached_predictions() -> Dict[str, Optional[str]]:
    """Prédictions (label prédit) du DERNIER résultat d'éval CACHÉ, par handle.
    Lecture SEULE du cache fichier écrit par cached_result() : n'exécute JAMAIS le
    LLM. Cache absent/illisible -> {} (fail-soft : predicted=None partout)."""
    if not RESULT_PATH.exists():
        return {}
    try:
        cached = json.loads(RESULT_PATH.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    return {r["handle"]: r.get("predicted_label") for r in cached.get("rows", [])}


def groundtruth_asof(as_of: Optional[str] = None) -> dict:
    """Jeu de preuve DATÉ : lignes du CSV annotées à `as_of` inclus (défaut :
    toutes). Journal append-only -> filtre lexicographique `annotated_at <= as_of`
    (les dates ISO YYYY-MM-DD se comparent comme des chaînes). Enrichit chaque
    ligne de la prédiction du dernier résultat d'éval CACHÉ (jamais de LLM) et d'un
    drapeau de désaccord (mapping v2bis). -> {as_of effectif, total, rows}."""
    preds = _cached_predictions()
    gt = load_groundtruth()
    dates = sorted({r.get("annotated_at", "").strip()
                    for r in gt if r.get("annotated_at", "").strip()})
    effective = as_of or (dates[-1] if dates else None)
    rows: List[dict] = []
    for row in gt:
        annotated_at = row.get("annotated_at", "").strip()
        if as_of and annotated_at and annotated_at > as_of:
            continue
        h = row["handle"].strip()
        true_label = row["label"].strip()
        predicted = preds.get(h)
        rows.append({
            "handle": h,
            "name": row.get("name", "").strip(),
            "label": true_label,
            "confidence": row.get("confidence", "").strip(),
            "rationale": row.get("rationale", "").strip(),
            "annotated_at": annotated_at,
            "ig_url": f"https://instagram.com/{h}",
            "has_snapshot": snapshot_path(h).exists(),
            "predicted": predicted,
            "disagreement": is_disagreement(true_label, predicted),
        })
    return {"as_of": effective, "total": len(rows), "rows": rows}


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
    print()
    print("Matrice de confusion par LABEL (vérité mappée -> label prédit) :")
    from .metrics import LABEL_ORDER
    matrix = result["labels_matrix"]
    cols = LABEL_ORDER
    print(f"  {'vérité':<16} " + " ".join(f"{c[:8]:>9}" for c in cols))
    for label in LABEL_ORDER:
        if label in matrix:
            row = matrix[label]
            print(f"  {label:<16} " + " ".join(f"{row.get(c, 0):>9}" for c in cols))
    print()
    hp = result.get("hot_precision")
    print(f"** PRÉCISION segment chaud : {_fmt_pct(hp)} **"
          f"   ({result.get('hot_tp', 0)} vrais / {result.get('hot_n', 0)} prédits opening_soon|just_opened)")
    ok = "OK" if result["gates_pass"] else "ÉCHEC"
    print(f"GATES : rappel opening>=100% = {result['gate_recall_opening']} | "
          f"précision chaud>=60% = {result['gate_hot_precision']}  -> {ok}")
    print(f"  (info) précision a_contacter>=33% = {result['gate_precision']}")
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
            "labels_matrix": result["labels_matrix"],
            "hot_precision": result["hot_precision"],
            "gates_pass": result["gates_pass"],
            "missing_snapshots": result["missing_snapshots"],
            "excluded_low_confidence": result["excluded_low_confidence"],
        }
        Path(args.json).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Rapport JSON écrit : {args.json}")
    import sys
    sys.exit(0 if result["gates_pass"] else 1)


if __name__ == "__main__":
    main()
