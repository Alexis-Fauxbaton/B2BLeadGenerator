"""Backfill one-shot de `Opportunity.followers_count` sur les fiches déjà en
base qui ont un handle Instagram mais pas encore de nombre d'abonnés (créées
avant l'ajout de ce champ).

Deux sources, dans cet ordre (la moins chère d'abord) :
  1. Cache disque des snapshots profils Apify déjà scrapés pour l'éval du juge
     prescripteur (`app/ingestion/eval/snapshots_architectes/<handle>.json`) —
     GRATUIT, aucun appel réseau.
  2. Pour les handles restants : re-scrape live via l'acteur Apify "profil"
     déjà utilisé par le funnel (`instagram.scrape_profiles`, nécessite
     `APIFY_TOKEN`). Fail-soft : sans token, ou si l'appel échoue, ces handles
     restent simplement NULL (VIDE > FAUX).

Usage :
  cd backend && .venv\\Scripts\\python.exe -m app.ingestion.backfill_followers
  # --dry-run : n'écrit rien, affiche seulement ce qui serait mis à jour.
  # --limit N : plafonne le nb de handles envoyés à Apify (protection budget).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, Optional

from sqlmodel import Session, select

from .. import database
from ..models import Opportunity
from . import instagram as instagram_mod

SNAP_DIR = Path(__file__).resolve().parent / "eval" / "snapshots_architectes"


def _snapshot_followers(handle: str) -> Optional[int]:
    """Lit followersCount depuis le snapshot disque, si présent. None sinon."""
    path = SNAP_DIR / f"{handle}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    n = data.get("followersCount")
    return n if isinstance(n, int) else None


def run(session: Session, *, dry_run: bool = False, limit: Optional[int] = None) -> Dict[str, int]:
    """Backfill sur la session donnée. Renvoie un résumé {from_snapshot, from_apify,
    still_missing, updated}. Ne committe PAS si `dry_run`."""
    rows = session.exec(
        select(Opportunity).where(
            Opportunity.instagram.is_not(None),
            Opportunity.followers_count.is_(None),
        )
    ).all()

    stats = {"candidates": len(rows), "from_snapshot": 0, "from_apify": 0,
              "still_missing": 0, "updated": 0}

    remaining: Dict[str, Opportunity] = {}
    for opp in rows:
        handle = (opp.instagram or "").strip().lstrip("@").lower()
        if not handle:
            stats["still_missing"] += 1
            continue
        n = _snapshot_followers(handle)
        if n is not None:
            stats["from_snapshot"] += 1
            if not dry_run:
                opp.followers_count = n
                session.add(opp)
            stats["updated"] += 1
        else:
            remaining[handle] = opp

    # Re-scrape Apify pour les handles sans snapshot (budget-gated par --limit).
    handles = list(remaining.keys())
    if limit is not None:
        handles = handles[:limit]
    if handles:
        profiles = instagram_mod.scrape_profiles(handles)  # fail-soft {} sans token/erreur
        for handle, opp in remaining.items():
            prof = profiles.get(handle)
            n = prof.get("followersCount") if prof else None
            if isinstance(n, int):
                stats["from_apify"] += 1
                if not dry_run:
                    opp.followers_count = n
                    session.add(opp)
                stats["updated"] += 1
            else:
                stats["still_missing"] += 1
    else:
        # Handles sans snapshot mais coupés par --limit -> comptés manquants.
        stats["still_missing"] += max(0, len(remaining) - len(handles))

    if not dry_run:
        session.commit()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                         help="N'écrit rien en base, affiche seulement le résumé.")
    parser.add_argument("--limit", type=int, default=None,
                         help="Plafonne le nb de handles envoyés à Apify (protection budget).")
    args = parser.parse_args()

    database.init_db()
    with Session(database.engine) as session:
        stats = run(session, dry_run=args.dry_run, limit=args.limit)

    print("Backfill followers_count :")
    print(f"  candidats (instagram non-null, followers_count NULL) : {stats['candidates']}")
    print(f"  trouvés via snapshot disque (gratuit)                : {stats['from_snapshot']}")
    print(f"  trouvés via re-scrape Apify                          : {stats['from_apify']}")
    print(f"  toujours manquants (VIDE > FAUX)                     : {stats['still_missing']}")
    print(f"  lignes mises à jour{' (dry-run, non committé)' if args.dry_run else ''} : {stats['updated']}")


if __name__ == "__main__":
    main()
