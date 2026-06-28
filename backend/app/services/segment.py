"""Segmentation des leads : lieu physique vs service/à domicile.

Pertinent pour un fournisseur d'aménagement de lieu (LumaPro) : un restaurant /
hôtel / bar à vitrine est un bon prospect ; un traiteur-services ou une activité
à domicile l'est beaucoup moins (pas de salle à équiper) — et il est aussi
quasi introuvable côté contact. On l'utilise pour déprioriser dans le scoring.
"""
from __future__ import annotations

from typing import Optional

VENUE = "venue"
SERVICE = "service"

# Types CHR correspondant à un lieu physique à aménager.
VENUE_TYPES = {"restaurant", "hôtel", "bar", "brasserie", "café", "coffee shop"}

# NAF des services de traiteurs (souvent sans vitrine / à domicile).
SERVICE_NAF = {"56.21Z"}


def classify_segment(
    establishment_type: Optional[str],
    naf: Optional[str] = None,
    name: Optional[str] = None,
) -> str:
    """Renvoie "venue" (lieu physique) ou "service" (traiteur/à domicile)."""
    code = (naf or "").strip().upper().replace(" ", "")
    if code in SERVICE_NAF:
        return SERVICE
    if (establishment_type or "").lower() == "traiteur":
        return SERVICE
    if (establishment_type or "").lower() in VENUE_TYPES:
        return VENUE
    # Par défaut, on ne pénalise pas (inconnu = neutre -> traité comme venue).
    return VENUE
