"""Métriques d'éval de la classification PRESCRIPTEURS (A1) — fonctions PURES.

Entrée = paires (label_vérité, label_prédit) et/ou lignes {true_label, tier}.
Gate principal : précision de studio_actif (un studio_actif prédit EST-il un vrai
studio_actif ?). Gate dur : 0 hors_cible vrai rangé dans un tier chaud (T1/T2)."""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Optional, Tuple

Pair = Tuple[str, str]

LABEL_ORDER = ["studio_actif", "studio_dormant", "compte_perso", "hors_cible"]


def studio_actif_precision(pairs: List[Pair]) -> Tuple[Optional[float], int, int]:
    """(vrais studio_actif parmi les prédits studio_actif) / prédits studio_actif.
    -> (précision|None, vrais_positifs, total_prédits). None si aucun prédit."""
    predicted = [truth for truth, pred in pairs if pred == "studio_actif"]
    if not predicted:
        return None, 0, 0
    tp = sum(1 for truth in predicted if truth == "studio_actif")
    return tp / len(predicted), tp, len(predicted)


def hors_cible_in_tiers(rows: List[dict]) -> List[str]:
    """Handles dont la VÉRITÉ = hors_cible mais rangés en tier chaud (T1/T2).
    DOIT être vide (gate dur : 0 hors_cible en T1/T2)."""
    return [r["handle"] for r in rows
            if r.get("true_label") == "hors_cible" and r.get("tier") in ("T1", "T2")]


def false_merges_cross_source(pairs, truth_same_studio) -> List[Pair]:
    """Paires (ref_entrante, ref_existante) EFFECTIVEMENT fusionnées par le pipeline
    (`stats.soft_merges`, source entrante ∈ SOFT_DEDUP_SOURCES = annuaire /
    sirene_stock / places) -> celles NON justifiées par la vérité (studios
    différents) = FAUX MERGES. `truth_same_studio` : ensemble des paires annotées
    comme le MÊME studio. DOIT être vide (gate dur : 0 faux merge cross-source,
    fixture adverse homonyme même CP incluse). PURE.

    Généralise l'ancien gate A2 annuaire×insta (`false_merges_annuaire_insta`, dont
    ce nom reste un alias) : la MÊME métrique couvre désormais les fusions douces
    des sources de MASSE (sirene_stock↔places, ↔insta), toujours alimentée par les
    fusions RÉELLEMENT émises — jamais court-circuitée.

    N.B. : ne mesure QUE les fusions réellement émises (les non-fusions ne peuvent
    pas être un faux merge). Le rappel (studios identiques NON fusionnés) est laissé
    à la doctrine VIDE > FAUX : rater une fusion est acceptable, en inventer une non."""
    return [p for p in pairs if tuple(p) not in truth_same_studio]


# Rétro-compat : l'ancien nom (A2) pointe vers la métrique généralisée cross-source.
false_merges_annuaire_insta = false_merges_cross_source


def label_confusion(pairs: List[Pair]) -> Dict[str, Dict[str, int]]:
    matrix: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for truth, pred in pairs:
        matrix[truth][pred] += 1
    return {t: dict(row) for t, row in matrix.items()}
