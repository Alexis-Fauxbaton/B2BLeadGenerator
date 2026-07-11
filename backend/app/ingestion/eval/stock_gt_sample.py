"""Échantillonneur GROUND-TRUTH du STOCK Sirene (B, T6) — outil du « Tir ».

Le stock `sirene_stock` (74.10Z qualifié mots-clés) porte ~9,3 % du stock brut
et, parmi les qualifiés, la sonde mesure ~39 % de vrais studios d'intérieur,
~50 % d'ambigus (cible plausible à téléphoner) et ~11 % de faux-amis clairs
(design graphique/produit, fit-out corporate). Pour MESURER la précision réelle
en production, ce script :

  1. tire N leads `sirene_stock` AU HASARD de la base (échantillon représentatif,
     graine RNG paramétrable pour la reproductibilité) ;
  2. écrit un CSV à annoter à la main (`handle`/`denomination`/`siren`/`ville`
     + colonne `label` VIDE) ;
  3. recalcule la précision depuis le CSV annoté avec un gate PARAMÉTRABLE.

Convention d'annotation de la colonne `label` (VIDE = non annoté, ignoré) :
  - `cible`      : vrai studio d'architecture/décoration d'intérieur, ou cible
                   plausible à contacter (les ambigus « STUDIO X »/« X DESIGN »
                   comptent CIBLE — closers au téléphone, doctrine VIDE > FAUX) ;
  - `hors_cible` : faux-ami clair (design graphique/produit, fit-out corporate,
                   hors périmètre archi d'intérieur).
La précision = `cible / (cible + hors_cible)` sur les seules lignes annotées.

Usage (SYNCHRONE, depuis backend/, PYTHONIOENCODING=utf-8) :
  # 1) tirer 100 leads stock au hasard -> CSV à annoter
  python -m app.ingestion.eval.stock_gt_sample --sample 100 --out stock_gt.csv
  # 2) après annotation manuelle de la colonne `label`
  python -m app.ingestion.eval.stock_gt_sample --score stock_gt.csv --min-precision 0.70

Aucun réseau. Le tirage lit la base locale (SQLite) ; le scoring ne lit que le
CSV. `sample_stock_leads`/`stock_precision` sont PURES (session/lignes injectées),
testées sans base réelle."""
from __future__ import annotations

import argparse
import csv
import random
import sys
from typing import Dict, List, Optional, Set, Tuple

from sqlmodel import Session, select

from ...models import Opportunity

# En-tête du CSV d'annotation. `handle` = identifiant stable du lead
# (`source_ref`, c.-à-d. le SIRET pour le stock).
SAMPLE_HEADER = ("handle", "denomination", "siren", "ville", "label")

# Vocabulaire d'annotation par défaut (voir docstring). Normalisé en minuscules.
POSITIVE_LABELS: Set[str] = {"cible"}
NEGATIVE_LABELS: Set[str] = {"hors_cible"}

DEFAULT_MIN_PRECISION = 0.70


def sample_stock_leads(
    session: Session, n: int, rng: Optional[random.Random] = None,
) -> List[Dict[str, str]]:
    """Tire `n` leads `sirene_stock` (population architecte) AU HASARD de la base
    (bornés au nombre disponible). Renvoie des dicts prêts pour le CSV
    (`SAMPLE_HEADER`, `label` VIDE). `rng` injectable pour la reproductibilité.

    Tous les leads `sirene_stock` en base sont qualifiés par construction (le
    connecteur `SireneStockConnector` n'émet que des candidats retenus par le
    filtre mots-clés) -> aucun filtre supplémentaire n'est requis ici. PURE-ish
    (session injectée, aucun réseau)."""
    rng = rng or random.Random()
    rows = session.exec(
        select(Opportunity).where(
            Opportunity.source == "sirene_stock",
            Opportunity.population == "architecte",
        )
    ).all()
    picked = rng.sample(rows, min(n, len(rows))) if rows else []
    return [{
        "handle": o.source_ref or (o.siret or ""),
        "denomination": o.establishment_name or "",
        "siren": o.siren or "",
        "ville": o.city or "",
        "label": "",
    } for o in picked]


def write_sample_csv(rows: List[Dict[str, str]], path: str) -> None:
    """Écrit les lignes d'échantillon au format CSV (`SAMPLE_HEADER`), UTF-8."""
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(SAMPLE_HEADER))
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, "") for k in SAMPLE_HEADER})


def load_annotated(path: str) -> List[Dict[str, str]]:
    """Relit un CSV annoté (DictReader, UTF-8)."""
    with open(path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def stock_precision(
    rows: List[Dict[str, str]],
    positive: Optional[Set[str]] = None,
    negative: Optional[Set[str]] = None,
) -> Tuple[Optional[float], int, int]:
    """Précision du stock depuis les lignes annotées : `positifs / annotés`.
    Ne compte QUE les lignes dont le `label` (normalisé minuscules/espaces) est
    dans `positive` ∪ `negative` (les VIDES et labels inconnus sont ignorés).
    -> `(précision|None, positifs, annotés)`. None si aucune ligne annotée. PURE."""
    positive = {s.lower() for s in (positive or POSITIVE_LABELS)}
    negative = {s.lower() for s in (negative or NEGATIVE_LABELS)}
    tp = 0
    n = 0
    for r in rows:
        lab = (r.get("label") or "").strip().lower()
        if lab in positive:
            tp += 1
            n += 1
        elif lab in negative:
            n += 1
    if n == 0:
        return None, 0, 0
    return tp / n, tp, n


def _run_sample(n: int, out: str, seed: Optional[int]) -> int:
    from ...database import engine
    rng = random.Random(seed) if seed is not None else random.Random()
    with Session(engine) as s:
        rows = sample_stock_leads(s, n, rng=rng)
    if not rows:
        print("Aucun lead `sirene_stock` en base — rien à échantillonner.")
        return 1
    write_sample_csv(rows, out)
    print(f"{len(rows)} leads stock tirés -> {out} "
          f"(annoter la colonne `label` : cible | hors_cible).")
    return 0


def _run_score(path: str, min_precision: float) -> int:
    rows = load_annotated(path)
    prec, tp, n = stock_precision(rows)
    if prec is None:
        print(f"CSV {path} : aucune ligne annotée (colonne `label` vide). "
              "Annoter puis relancer.")
        return 1
    ok = prec >= min_precision
    print("=" * 60)
    print("PRÉCISION STOCK Sirene (échantillon GT annoté)")
    print("=" * 60)
    print(f"Lignes annotées : {n}  (cible : {tp}, hors_cible : {n - tp})")
    print(f"** PRÉCISION : {prec * 100:.1f}% ** (gate >= {min_precision * 100:.0f}%)")
    print(f"GATE : {'OK' if ok else 'ÉCHEC'}")
    print("=" * 60)
    return 0 if ok else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Échantillon GT stock Sirene + calcul de précision (B, T6)")
    parser.add_argument("--sample", type=int, metavar="N",
                        help="tirer N leads sirene_stock au hasard -> CSV")
    parser.add_argument("--out", default="stock_gt.csv",
                        help="chemin du CSV d'échantillon (défaut stock_gt.csv)")
    parser.add_argument("--seed", type=int, default=None,
                        help="graine RNG (reproductibilité)")
    parser.add_argument("--score", metavar="PATH",
                        help="calculer la précision depuis un CSV annoté")
    parser.add_argument("--min-precision", type=float, default=DEFAULT_MIN_PRECISION,
                        help=f"gate de précision (défaut {DEFAULT_MIN_PRECISION})")
    args = parser.parse_args()

    if args.score:
        sys.exit(_run_score(args.score, args.min_precision))
    if args.sample is not None:
        sys.exit(_run_sample(args.sample, args.out, args.seed))
    parser.error("préciser --sample N ou --score PATH")


if __name__ == "__main__":
    main()
