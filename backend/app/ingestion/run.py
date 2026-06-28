"""CLI d'ingestion (ETL).

Modes :
  window       fenêtre fixe (--since jours) — le plus simple
  incremental  passe A : nouveaux leads depuis le dernier curseur (+ chevauchement)
  backfill     filet de sécurité : large fenêtre, comble les annonces manquées
  reenrich     passe B : guérit les leads sans NAF via Sirene (sans retaper BODACC)

Exemples :
    python -m app.ingestion.run --mode window --since 60 --limit 200
    python -m app.ingestion.run --mode incremental
    python -m app.ingestion.run --mode backfill --since 120
    python -m app.ingestion.run --mode reenrich
"""
import argparse

from .pipeline import (
    run_backfill,
    run_contact_enrich,
    run_incremental,
    run_ingestion,
    run_reenrich,
    stats_to_dict,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingestion de leads CHR (ETL).")
    parser.add_argument(
        "--mode",
        default="window",
        choices=["window", "incremental", "backfill", "reenrich", "contact"],
    )
    parser.add_argument("--source", default="bodacc", help="Connecteur à utiliser.")
    parser.add_argument("--since", type=int, default=90, help="Fenêtre en jours (window/backfill).")
    parser.add_argument("--limit", type=int, default=100, help="Nombre max d'annonces.")
    parser.add_argument(
        "--departments",
        default=None,
        help="Départements séparés par des virgules (ex: 75,92,93). Défaut : IdF.",
    )
    parser.add_argument("--reset", action="store_true", help="Supprime d'abord la source.")
    parser.add_argument(
        "--no-enrich",
        action="store_true",
        help="Désactive l'enrichissement Sirene.",
    )
    args = parser.parse_args()

    departments = (
        [d for d in args.departments.split(",") if d.strip()] if args.departments else None
    )
    enrich = not args.no_enrich

    print(f"Mode '{args.mode}' (source={args.source})...")

    if args.mode == "reenrich":
        stats = run_reenrich(source=args.source)
    elif args.mode == "contact":
        stats = run_contact_enrich(source=args.source)
    elif args.mode == "incremental":
        stats = run_incremental(source=args.source, departments=departments, enrich=enrich)
    elif args.mode == "backfill":
        stats = run_backfill(
            source=args.source, since_days=args.since, departments=departments, enrich=enrich
        )
    else:  # window
        stats = run_ingestion(
            source=args.source,
            since_days=args.since,
            limit=args.limit,
            departments=departments,
            reset=args.reset,
            enrich=enrich,
        )

    print("[OK] Termine :")
    for key, value in stats_to_dict(stats).items():
        print(f"   {key:<16} = {value}")
    if getattr(stats, "truncated", False):
        print(
            "   [!] Fenetre TRONQUEE : plus d'annonces disponibles que recuperees. "
            "Lancer un --mode backfill (ou augmenter --limit)."
        )


if __name__ == "__main__":
    main()
