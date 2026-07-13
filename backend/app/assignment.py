"""Filtre d'assignation partagé (opportunités + relances).

`assigned=me|none|<name>` :
- me      -> résolu via la SESSION (nom du user loggé) ; SANS session -> aucun
             résultat (« me » sans identité = rien n'est à moi).
- none    -> non assignés (assigned_to IS NULL) — filtre « Non assignés » du patron.
- <name>  -> assignés à ce nom exact.
- vide/absent -> pas de filtre.

Fonction PURE (pas de FastAPI) : `current_user` peut être un User, None, ou la
sentinelle Depends (appel direct en test) — d'où le garde `isinstance(..., User)`
comme dans `routes/activities.add_activity`.
"""
from typing import Optional

from sqlalchemy import false

from .models import Opportunity, User


def apply_assigned_filter(query, assigned: Optional[str], current_user):
    """Ajoute la clause WHERE d'assignation à `query` (SELECT Opportunity) et la
    renvoie. `query` inchangée si `assigned` est vide/absent."""
    if not assigned:
        return query
    if assigned == "none":
        return query.where(Opportunity.assigned_to.is_(None))
    if assigned == "me":
        if isinstance(current_user, User) and current_user.name:
            return query.where(Opportunity.assigned_to == current_user.name)
        # « me » sans session : rien n'est à personne -> aucun résultat.
        return query.where(false())
    return query.where(Opportunity.assigned_to == assigned)
