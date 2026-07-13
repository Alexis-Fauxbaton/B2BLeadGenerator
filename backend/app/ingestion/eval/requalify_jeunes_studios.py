"""Script ONE-SHOT — requalification des leads `jeunes_studios` (A2, délta).

Les 49 leads `source='jeunes_studios'` en base ont été ingérés avec l'ANCIEN
filtre `qualifies()` d'avant le resserrage GT (calibré ensuite sur le GT stock,
doctrine VIDE > FAUX — voir `jeunes_studios.py`). Ce script repasse chaque
dénomination au `qualifies()` ACTUEL et supprime (fiche + Signals + ContactHistory,
comme `pipeline._delete_opportunity`) celles qui échouent désormais.

SYNCHRONE, écrit en base. Faire une sauvegarde `.bak` de la base AVANT de lancer
(pas fait ici — le fichier DB n'est pas ce script). Usage (depuis backend/,
PYTHONIOENCODING=utf-8) :

  python -m app.ingestion.eval.requalify_jeunes_studios

`requalify` est injectable (session/lignes) pour les tests, aucun réseau."""
from __future__ import annotations

from typing import List, Tuple

from sqlmodel import Session, delete, select

from ...models import ContactHistory, Opportunity, Signal
from ..jeunes_studios import qualifies


def requalify_jeunes_studios(session: Session) -> Tuple[List[str], List[str]]:
    """Repasse chaque lead `source='jeunes_studios'` au `qualifies()` ACTUEL.
    Supprime (fiche + Signals + ContactHistory) ceux qui échouent. Renvoie
    `(noms_purges, noms_conserves)`. Commit à la fin (tout ou rien par ligne,
    pas de rollback partiel nécessaire — suppression pure, aucun réseau)."""
    rows = session.exec(
        select(Opportunity).where(Opportunity.source == "jeunes_studios")
    ).all()
    purged: List[str] = []
    kept: List[str] = []
    for opp in rows:
        if qualifies(opp.establishment_name):
            kept.append(opp.establishment_name)
            continue
        purged.append(opp.establishment_name)
        session.exec(delete(ContactHistory).where(ContactHistory.opportunity_id == opp.id))
        session.exec(delete(Signal).where(Signal.opportunity_id == opp.id))
        session.delete(opp)
    session.commit()
    return purged, kept


def main() -> None:
    from ...database import engine

    with Session(engine) as session:
        purged, kept = requalify_jeunes_studios(session)

    print("=" * 60)
    print("REQUALIFICATION jeunes_studios (filtre qualifies() actuel)")
    print("=" * 60)
    print(f"Purgés   : {len(purged)}")
    print(f"Conservés: {len(kept)}")
    if purged:
        print("\nNoms purgés :")
        for n in purged:
            print(f"  - {n}")
    print("=" * 60)


if __name__ == "__main__":
    main()
