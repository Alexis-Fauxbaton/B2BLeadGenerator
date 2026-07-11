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
    LABEL_ORDER, false_merges_annuaire_insta, hors_cible_in_tiers,
    label_confusion, studio_actif_precision,
)

ROOT = Path(__file__).resolve().parent
CSV_PATH = ROOT / "architectes_groundtruth.csv"
SNAP_DIR = ROOT / "snapshots_architectes"
ANNUAIRE_SNAP_DIR = ROOT / "annuaires_snapshots"

GATE_STUDIO_PRECISION = 0.70  # précision studio_actif >= 70 %
GATE_ANNUAIRE_STUDIO_ACTIF = 0.70  # >= 70 % des membres annuaire -> studio_actif


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

    # Gate A2 (annuaires) : 0 faux merge annuaire×insta + membres annuaire ->
    # studio_actif. Tourne sur les fixtures LIVRÉES (autonome, offline), toujours.
    annuaire = run_annuaires_gate()
    gate_false_merge = annuaire["gate_zero_false_merge"]
    gate_annuaire_sa = annuaire["gate_annuaire_studio_actif"]

    return {
        "n": len(snapshots), "missing": missing,
        "studio_actif_precision": prec, "studio_actif_tp": tp, "studio_actif_n": n,
        "hors_cible_in_tiers": violations,
        "confusion": label_confusion(pairs),
        "gate_studio_precision": gate_precision,
        "gate_zero_hors_cible_in_tiers": gate_tiers,
        "annuaire": annuaire,
        "gate_zero_false_merge": gate_false_merge,
        "gate_annuaire_studio_actif": gate_annuaire_sa,
        "gates_pass": (gate_precision and gate_tiers
                       and gate_false_merge and gate_annuaire_sa),
        "rows": detail_rows,
    }


def _annuaire_pages() -> Dict[str, str]:
    """Sert les fixtures HTML LIVRÉES (annuaires_snapshots/) au connecteur CFAI :
    URL -> HTML. Déterministe, offline (aucun réseau)."""
    def _read(name: str) -> str:
        return (ANNUAIRE_SNAP_DIR / name).read_text(encoding="utf-8")

    base = "https://www.cfai.fr"
    return {
        f"{base}/fr/recherche/annuaire-professionnel?page=1": _read("cfai-list.html"),
        f"{base}/annuaire-professionnel/adherent/12": _read("cfai-adherent-12.html"),
        f"{base}/annuaire-professionnel/adherent/77": _read("cfai-adherent-77.html"),
    }


class _NoSireneEnricher:
    """Enrichisseur SIREN factice (offline) : aucun lookup registre."""

    def lookup(self, siren):  # noqa: D401, ANN001
        return None


def run_annuaires_gate() -> dict:
    """Gate 0 FAUX MERGE annuaire×insta (A2, T5), autonome et OFFLINE.

    Tourne `run_annuaires` de bout en bout sur les fixtures LIVRÉES
    (annuaires_snapshots/) + des fiches Instagram pré-semées en DB mémoire, avec un
    matcher/sirene déterministes injectés. Le mini-jeu exerce RÉELLEMENT la métrique :
      (a) un couple annuaire×insta LÉGITIME (Metropole Concept, corroboré par le
          domaine de site) -> fusion attendue, annotée "même studio" -> PAS un faux
          merge ;
      (b) un homonyme DISTINCT même nom+ville SANS corroboration (Studio Homonyme,
          site et code postal différents) -> ne DOIT PAS fusionner.
    Le gate consomme les paires RÉELLEMENT fusionnées (`stats.soft_merges`) — jamais
    court-circuité à True. Le run réel borné + l'annotation navigateur ÉTENDENT le GT
    (architectes_groundtruth.csv) au-delà de ces fixtures ; ce gate reste autonome.
    """
    from sqlmodel import Session, SQLModel, create_engine, select

    from ..annuaires.cfai import CfaiConnector
    from ..base import LeadCandidate
    from ..pipeline import IngestStats, _process_candidate, run_annuaires
    from ...models import Opportunity

    pages = _annuaire_pages()
    fetch = lambda u: pages.get(u)  # noqa: E731

    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    today = date(2026, 7, 11)

    # Paires annotées "même studio" (vérité offline). Le couple Metropole Concept
    # est le MÊME studio des deux côtés (annuaire CFAI + compte Insta).
    truth_same_studio = {("cfai:12", "metropole_concept")}

    with Session(engine) as s:
        # (a) compte Insta légitime : même domaine de site que la fiche CFAI #12.
        _process_candidate(s, LeadCandidate(
            source="instagram", source_ref="metropole_concept",
            establishment_name="Metropole Concept", city="Paris", address="",
            website="http://www.metropole-concept.com",
            main_signal="prescripteur actif", detection_date=today,
            establishment_type="architecte d'intérieur", population="architecte"),
            IngestStats(source="instagram"), set(), None)
        # (b) homonyme DISTINCT : même nom+ville que la fiche CFAI #77 mais AUCUNE
        # corroboration (site différent, pas de code postal partagé).
        _process_candidate(s, LeadCandidate(
            source="instagram", source_ref="studio_homonyme_insta",
            establishment_name="Studio Homonyme", city="Paris", address="",
            website="https://studio-homonyme-autre.fr",
            main_signal="prescripteur actif", detection_date=today,
            establishment_type="architecte d'intérieur", population="architecte"),
            IngestStats(source="instagram"), set(), None)
        s.commit()

        stats = run_annuaires("cfai", limit=10, max_pages=1, session=s,
                              http_fetch=fetch, matcher=lambda **k: None,
                              sirene=_NoSireneEnricher())

        false_merges = false_merges_annuaire_insta(stats.soft_merges, truth_same_studio)
        rows = s.exec(select(Opportunity).where(
            Opportunity.population == "architecte")).all()

    # Taux studio_actif des MEMBRES annuaire (candidats produits par le connecteur,
    # honoraires déjà écartés en amont par parse_fiche -> non comptés).
    conn = CfaiConnector(http_fetch=fetch)
    cands = conn.to_candidates(conn.fetch(limit=10, max_pages=1))
    n_studio = sum(1 for c in cands if c.lifecycle_label == "studio_actif")
    rate = (n_studio / len(cands)) if cands else 1.0

    gate_zero_false_merge = false_merges == []
    gate_annuaire_studio_actif = rate >= GATE_ANNUAIRE_STUDIO_ACTIF
    return {
        "soft_merges": [list(p) for p in stats.soft_merges],
        "false_merges": [list(p) for p in false_merges],
        "gate_zero_false_merge": gate_zero_false_merge,
        "annuaire_members": len(cands),
        "annuaire_studio_actif": n_studio,
        "studio_actif_rate": rate,
        "gate_annuaire_studio_actif": gate_annuaire_studio_actif,
        "architecte_rows_after": len(rows),
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
    ann = res.get("annuaire")
    if ann is not None:
        print("-" * 60)
        print("ANNUAIRES (A2) — gate 0 faux merge annuaire×insta (fixtures livrées)")
        print(f"  fusions douces réelles : {ann['soft_merges']}")
        print(f"  FAUX merges (doit être vide) : {ann['false_merges']}")
        print(f"  membres annuaire -> studio_actif : {ann['annuaire_studio_actif']}"
              f"/{ann['annuaire_members']} ({ann['studio_actif_rate']*100:.0f}%)")
    ok = "OK" if res["gates_pass"] else "ÉCHEC"
    print(f"GATES : précision studio_actif>=70% = {res['gate_studio_precision']} | "
          f"0 hors_cible en T1/T2 = {res['gate_zero_hors_cible_in_tiers']} | "
          f"0 faux merge = {res.get('gate_zero_false_merge')} | "
          f"membres annuaire studio_actif>=70% = {res.get('gate_annuaire_studio_actif')}"
          f" -> {ok}")
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
