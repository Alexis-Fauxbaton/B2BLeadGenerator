"""Calcul des métriques d'éval — fonctions PURES, sans réseau ni I/O.

Entrée = une liste de paires `(label_vérité, bucket_prédit)`. Le bucket cible
est `a_contacter` : seul le label `opening` a le droit d'y tomber (cf. README).

Métriques :
- précision de `a_contacter` = vrais `opening` parmi tout ce qui est classé
  `a_contacter` (LA métrique n°1 : « opening soon = opening soon ? ») ;
- rappel des `opening` = `opening` retrouvés en `a_contacter` / `opening` totaux ;
- matrice de confusion `label -> bucket_prédit`.

Testé sur un mini-jeu jouet (voir tests/test_eval.py)."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

A_CONTACTER = "a_contacter"
OPENING = "opening"

Pair = Tuple[str, str]  # (label_vérité, bucket_prédit)


@dataclass
class EvalReport:
    n: int  # nb de comptes évalués
    n_a_contacter: int  # nb classés a_contacter (dénominateur précision)
    tp_opening: int  # opening correctement classés a_contacter
    precision_a_contacter: Optional[float]  # None si aucun a_contacter prédit
    n_opening: int  # nb d'opening dans la vérité (dénominateur rappel)
    recall_opening: Optional[float]  # None si aucun opening dans la vérité
    confusion: Dict[str, Dict[str, int]] = field(default_factory=dict)
    false_positives: List[Pair] = field(default_factory=list)  # a_contacter mais label != opening

    def as_dict(self) -> dict:
        return {
            "n": self.n,
            "n_a_contacter": self.n_a_contacter,
            "tp_opening": self.tp_opening,
            "precision_a_contacter": self.precision_a_contacter,
            "n_opening": self.n_opening,
            "recall_opening": self.recall_opening,
            "confusion": {k: dict(v) for k, v in self.confusion.items()},
        }


def confusion_matrix(pairs: List[Pair]) -> Dict[str, Dict[str, int]]:
    """`label_vérité -> {bucket_prédit -> compte}`."""
    matrix: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for label, predicted in pairs:
        matrix[label][predicted] += 1
    return {label: dict(row) for label, row in matrix.items()}


# Ordre d'affichage des labels de cycle de vie (funnel v2).
LABEL_ORDER = [
    "opening_soon", "just_opened", "renovation", "unknown",
    "established", "chain_multisite", "not_venue", "noise",
]


def label_confusion(pairs: List[Pair]) -> Dict[str, Dict[str, int]]:
    """Matrice `label_vérité -> {label_prédit -> compte}` (funnel v2). Identique
    en forme à confusion_matrix, mais les deux axes sont des labels de cycle de
    vie (pas des buckets binaires)."""
    return confusion_matrix(pairs)


# Segment « chaud » : prédictions à traiter en priorité (funnel v2bis).
# renovation (établi EN TRAVAUX) est un segment chaud au même titre qu'une
# ouverture (fenêtre d'aménagement ouverte).
HOT_PRED = {"opening_soon", "just_opened", "renovation"}
# Vérités (déjà MAPPÉES : opening -> opening_soon) qui légitiment une prédiction
# chaude. unknown n'y est pas (une prédiction chaude sur un unknown vrai = FP).
HOT_TRUTH = {"opening_soon", "just_opened", "renovation"}


def hot_precision(label_pairs: List[Pair]) -> Tuple[Optional[float], int, int]:
    """Précision du segment chaud = (vérité chaude parmi les prédits chauds) /
    prédits chauds. `label_pairs` = (vérité_mappée, label_prédit).
    -> (précision|None, vrais_positifs, total_prédits_chauds). None si aucun
    prédit chaud. Métrique HONNÊTE : un `just_opened` prédit sur un vrai
    `just_opened` compte comme vrai positif (plus un faux positif du recall opening)."""
    hot = [(truth, pred) for truth, pred in label_pairs if pred in HOT_PRED]
    if not hot:
        return None, 0, 0
    tp = sum(1 for truth, _ in hot if truth in HOT_TRUTH)
    return tp / len(hot), tp, len(hot)


def bucket_precision(
    pairs: List[Pair], bucket: str = A_CONTACTER, target_label: str = OPENING
) -> Tuple[Optional[float], int, int]:
    """Précision d'un bucket = (label==target parmi les prédits==bucket) / prédits.
    -> (précision|None, vrais_positifs, total_prédit)."""
    predicted = [label for label, pred in pairs if pred == bucket]
    if not predicted:
        return None, 0, 0
    tp = sum(1 for label in predicted if label == target_label)
    return tp / len(predicted), tp, len(predicted)


def label_recall(
    pairs: List[Pair], label: str = OPENING, bucket: str = A_CONTACTER
) -> Tuple[Optional[float], int, int]:
    """Rappel d'un label vers un bucket = (label ET prédit==bucket) / (label total).
    -> (rappel|None, vrais_positifs, total_label)."""
    total = [pred for lab, pred in pairs if lab == label]
    if not total:
        return None, 0, 0
    tp = sum(1 for pred in total if pred == bucket)
    return tp / len(total), tp, len(total)


def summarize(pairs: List[Pair]) -> EvalReport:
    """Rapport complet à partir des paires (label, bucket_prédit)."""
    precision, tp, n_pred = bucket_precision(pairs)
    recall, _, n_opening = label_recall(pairs)
    fps = [(label, pred) for label, pred in pairs if pred == A_CONTACTER and label != OPENING]
    return EvalReport(
        n=len(pairs),
        n_a_contacter=n_pred,
        tp_opening=tp,
        precision_a_contacter=precision,
        n_opening=n_opening,
        recall_opening=recall,
        confusion=confusion_matrix(pairs),
        false_positives=fps,
    )
