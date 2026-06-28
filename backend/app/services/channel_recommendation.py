"""Service de recommandation du canal de contact.

Logique volontairement simple et explicable pour le PoC.
Renvoie le canal recommandé + une explication courte.
"""
from dataclasses import dataclass
from typing import List, Optional

STRUCTURED_TYPES = {"hôtel"}
SOCIAL_SIGNALS = {"compte instagram récent", "travaux visibles", "annonce presse locale"}
OPENING_SIGNALS = {"ouverture prochaine", "création récente", "nouveau point de vente"}
TAKEOVER_SIGNALS = {"reprise", "changement propriétaire"}


@dataclass
class ChannelResult:
    channel: str
    reason: str


def recommend_channel(
    establishment_type: str,
    main_signal: str,
    secondary_signals: Optional[List[str]],
    decision_maker: Optional[str],
    has_social_presence: bool = False,
) -> ChannelResult:
    secondary_signals = [s.lower() for s in (secondary_signals or [])]
    all_signals = {main_signal.lower(), *secondary_signals}
    establishment_type = establishment_type.lower()

    is_opening = bool(all_signals & OPENING_SIGNALS)
    is_takeover = bool(all_signals & TAKEOVER_SIGNALS)
    is_structured = establishment_type in STRUCTURED_TYPES
    has_social = has_social_presence or bool(all_signals & SOCIAL_SIGNALS)

    # 1. Reprise / changement de propriétaire -> téléphone (contact direct efficace)
    if is_takeover:
        return ChannelResult(
            channel="telephone",
            reason=(
                "Téléphone recommandé car il s'agit d'une reprise ou d'un changement "
                "de propriétaire : le contact direct est souvent plus efficace."
            ),
        )

    # 2. Hôtel / établissement structuré -> email B2B
    if is_structured:
        return ChannelResult(
            channel="email",
            reason=(
                "Email recommandé car l'établissement est structuré et le besoin "
                "est probablement professionnel et planifié."
            ),
        )

    # 3. Indépendant en ouverture avec signal social -> Instagram
    if is_opening and has_social:
        return ChannelResult(
            channel="instagram",
            reason=(
                "Instagram recommandé car l'établissement semble en phase d'ouverture "
                "et dispose d'un signal social récent."
            ),
        )

    # 4. Décideur identifié -> LinkedIn
    if decision_maker:
        return ChannelResult(
            channel="linkedin",
            reason=(
                f"LinkedIn recommandé car un décideur est identifié "
                f"({decision_maker}) : l'approche professionnelle est pertinente."
            ),
        )

    # 5. Par défaut -> téléphone
    return ChannelResult(
        channel="telephone",
        reason=(
            "Téléphone recommandé par défaut : aucun signal de canal clair, "
            "l'appel reste le moyen le plus fiable d'entrer en contact."
        ),
    )
