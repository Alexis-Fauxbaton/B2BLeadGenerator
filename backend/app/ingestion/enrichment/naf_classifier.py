"""Classification CHR à partir du code NAF/APE (fiable, officiel).

Le NAF donne le type de base ; on l'affine ensuite par mots-clés pour les
sous-types que le NAF ne distingue pas (café, brasserie, coffee shop, qui
relèvent tous de 56.10A ou 56.30Z).

Renvoie None si le NAF n'est pas un NAF CHR pertinent -> permet d'écarter les
faux positifs de la recherche plein-texte (ex: holdings, commerces de gros).
"""
from __future__ import annotations

from typing import Optional

from ..chr_classifier import classify as keyword_classify

# Code NAF (préfixe) -> type CHR de base.
NAF_TO_TYPE = {
    "55.10Z": "hôtel",
    "55.20Z": "hôtel",
    "56.10A": "restaurant",  # restauration traditionnelle
    "56.10B": "restaurant",  # cafétérias et libres-services
    "56.10C": "restaurant",  # restauration rapide
    "56.21Z": "traiteur",    # services des traiteurs
    "56.29B": "traiteur",    # autres services de restauration
    "56.30Z": "bar",         # débits de boissons
}

# NAF explicitement écartés (restauration collective sous contrat, etc.).
NAF_EXCLUDED = {"56.29A"}

# Sous-types détectables par mots-clés, prioritaires sur le type de base.
_REFINEMENTS = ("coffee shop", "brasserie", "café")


def _normalize_naf(naf: Optional[str]) -> str:
    if not naf:
        return ""
    return naf.strip().upper().replace(" ", "")


def classify_naf(naf: Optional[str], text: str = "") -> Optional[str]:
    """Type CHR à partir du NAF (+ affinage par mots-clés), ou None."""
    code = _normalize_naf(naf)
    if not code:
        return None
    if code in NAF_EXCLUDED:
        return None

    base = NAF_TO_TYPE.get(code)
    if not base:
        return None

    # Affinage : un mot-clé plus précis dans l'enseigne/activité prime.
    refined = keyword_classify(text)
    if refined in _REFINEMENTS:
        return refined

    return base
