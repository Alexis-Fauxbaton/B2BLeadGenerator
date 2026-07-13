"""Script ONE-SHOT — corrige les establishment_name « mode d'exercice » (CFAI).

Le champ « société » des fiches CFAI est en saisie LIBRE : certains membres y
décrivent leur mode d'exercice (« Exercice en libéral », « salariée chez
alinea », « En libéral depuis 2006 »…) au lieu d'un nom d'enseigne. Le
connecteur prenait ce champ tel quel comme establishment_name -> fiches UI
illisibles. Le connecteur est corrigé (`cfai._societe_is_placeholder`) ; ce
script repasse la MÊME heuristique sur les leads `source_ref LIKE 'cfai:%'`
déjà en base et remplace le descriptif par le nom du décideur
(`decision_maker`). Sans décideur, on ne touche pas (VIDE > FAUX).

SYNCHRONE, écrit en base. `main()` fait une sauvegarde
`chr_signal_radar.db.bak-cfaisociete-<ts>` AVANT toute écriture, puis imprime
avant -> après pour chaque ligne corrigée. Usage (depuis backend/,
PYTHONIOENCODING=utf-8) :

  python -m app.ingestion.eval.backfill_cfai_societe

`backfill` est injectable (session) pour les tests, aucun réseau."""
from __future__ import annotations

from typing import List, Tuple

from sqlmodel import Session, col, select

from ...models import Opportunity
from ..annuaires.cfai import _societe_is_placeholder


def backfill(session: Session) -> List[Tuple[int, str, str]]:
    """Remplace les establishment_name « mode d'exercice » des leads CFAI par
    le nom du décideur. Ne touche que `source_ref LIKE 'cfai:%'` AVEC
    `decision_maker` non vide (le champ société d'autres sources n'a pas cette
    sémantique de saisie libre). Renvoie [(id, avant, après)] ; commit final."""
    rows = session.exec(
        select(Opportunity).where(col(Opportunity.source_ref).like("cfai:%"))
    ).all()
    fixed: List[Tuple[int, str, str]] = []
    for opp in rows:
        decideur = (opp.decision_maker or "").strip()
        if not decideur or not _societe_is_placeholder(opp.establishment_name or ""):
            continue
        fixed.append((opp.id, opp.establishment_name, decideur))
        opp.establishment_name = decideur
        session.add(opp)
    session.commit()
    return fixed


def main() -> None:
    import shutil
    from datetime import datetime
    from pathlib import Path

    from ...database import DATABASE_URL, engine

    # Sauvegarde AVANT toute écriture (même doctrine que les autres one-shot).
    if DATABASE_URL.startswith("sqlite:///"):
        db_path = Path(DATABASE_URL[len("sqlite:///"):])
        if db_path.exists():
            bak = db_path.with_name(
                f"{db_path.name}.bak-cfaisociete-{datetime.now():%Y%m%d-%H%M%S}")
            shutil.copy2(db_path, bak)
            print(f"Sauvegarde : {bak}")

    with Session(engine) as session:
        fixed = backfill(session)

    print("=" * 60)
    print("BACKFILL CFAI — champ société « mode d'exercice »")
    print("=" * 60)
    print(f"Corrigés : {len(fixed)}")
    for oid, before, after in fixed:
        print(f"  #{oid}: {before!r} -> {after!r}")
    print("=" * 60)


if __name__ == "__main__":
    main()
