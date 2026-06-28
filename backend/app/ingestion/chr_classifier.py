"""Classification CHR (le cœur du "Transform").

À partir d'un texte libre (activité déclarée + dénomination), on décide :
  - est-ce un établissement CHR pertinent pour le fournisseur ?
  - si oui, quel type (restaurant, café, hôtel, bar, brasserie, traiteur, coffee shop) ?

Isolé et testable : facile à enrichir sans toucher au reste du pipeline.
Quelques faux positifs/négatifs sont assumés (l'activité BODACC est parfois vide).
"""
from __future__ import annotations

import re
import unicodedata
from typing import List, Optional, Tuple

# Ordre important : les motifs les plus spécifiques d'abord.
# (type CHR, liste de mots-clés normalisés sans accents)
_RULES: List[Tuple[str, List[str]]] = [
    ("coffee shop", ["coffee shop", "coffee-shop", "coffeeshop"]),
    ("traiteur", ["traiteur", "traiteurs"]),
    ("brasserie", ["brasserie"]),
    ("hôtel", ["hotel", "hotellerie", "hebergement hotelier", "hotel restaurant"]),
    (
        "bar",
        ["bar", "bar a vin", "bar a cocktails", "debit de boissons", "pub", "taverne"],
    ),
    ("café", ["cafe", "salon de the", "coffee", "cafeteria"]),
    (
        "restaurant",
        [
            "restaurant",
            "restauration",
            "restauration rapide",
            "restauration traditionnelle",
            "pizzeria",
            "creperie",
            "snack",
            "sandwicherie",
            "food truck",
        ],
    ),
]

# Mots-clés qui disqualifient (réduisent les faux positifs). Inclut des métiers
# voisins qui contiennent ou évoquent un mot CHR sans en être :
#  - "restauration d'objets/meubles" (ébénisterie), "coiffure/barber" (≠ bar)...
_EXCLUSIONS = [
    "restaurant d'entreprise",
    "restaurant administratif",
    "restauration collective",
    "restauration d'objets",
    "restauration de meubles",
    "restauration de mobilier",
    "restauration d'oeuvres",
    "restauration du patrimoine",
    "coiffure",
    "barbershop",
    "barber shop",
    "grossiste",
    "import-export",
    "vente a distance",
]


def _normalize(text: str) -> str:
    text = text.lower()
    text = "".join(
        c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn"
    )
    return " ".join(text.split())


def _matches(keyword: str, norm_text: str) -> bool:
    """Match par mot entier (word boundary) pour éviter qu'un mot court comme
    "bar" ne matche dans "barbershop"."""
    pattern = r"\b" + re.escape(_normalize(keyword)) + r"\b"
    return re.search(pattern, norm_text) is not None


def classify(text: str) -> Optional[str]:
    """Retourne le type CHR détecté, ou None si non pertinent."""
    if not text:
        return None

    norm = _normalize(text)

    for excl in _EXCLUSIONS:
        if _matches(excl, norm):
            return None

    for chr_type, keywords in _RULES:
        for kw in keywords:
            if _matches(kw, norm):
                return chr_type

    return None


def is_chr(text: str) -> bool:
    return classify(text) is not None
