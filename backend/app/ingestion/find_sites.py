"""CLI de découverte de site (Brique A du « chantier fiches gratuit »).

Cible les fiches SANS site d'une population (et, en option, d'une source),
appelle ``site_finder.find_site`` par fiche, patron calqué sur
``enrich_phones.py`` (cibles, commit PAR FICHE, stats, ``main()`` argparse).

Doctrine identique : VIDE > FAUX, verrou d'identité non négociable (porté par
``site_finder``), scraping poli. Ce CLI ne fait QUE consommer
``site_finder.find_site`` et, en mode ``--apply``, écrire ``website`` — jamais
d'écrasement d'un champ déjà rempli.

``--dry-run`` (défaut, NON destructif) : n'écrit RIEN dans ``opportunities``
(le cache ``sitefind:`` peut être écrit si une session est fournie — c'est
voulu, cf. ``site_finder``). ``--apply`` : écrit ``website`` UNIQUEMENT sur
les fiches ``found`` dont le champ était vide, commit PAR FICHE (reprenable,
fail-soft). Un seul écrivain SQLite à la fois : ne JAMAIS lancer en parallèle
d'une autre passe d'enrichissement (B/C).

Usage :
    python -m app.ingestion.find_sites --population architecte --source sirene_stock \\
        --limit N [--dry-run | --apply] [--out chemin.jsonl]
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import date
from typing import List, Optional, TextIO

from sqlalchemy import or_
from sqlmodel import Session, select

from ..database import engine, init_db
from ..models import Opportunity
from .enrichment.site_finder import HtmlFetch, SiteFindResult, _polite_get, find_site


@dataclass
class SiteStats:
    """Compteurs d'un run de découverte de sites. ``search_unavailable`` (moteurs
    muets) est compté À PART de ``no_candidate`` (moteur servi, 0 candidat propre)
    : les premières sont RÉESSAYABLES (piloter la brique B), pas les secondes."""
    population: str = "architecte"
    source: str = "sirene_stock"
    scanned: int = 0
    found: int = 0
    locked_out: int = 0
    no_candidate: int = 0
    search_unavailable: int = 0
    errors: int = 0


def _site_targets(
    session: Session, population: str, source: Optional[str], limit: int,
) -> List[Opportunity]:
    """Fiches SANS site d'une population (et, si fourni, d'une source) — jamais
    de fiche déjà pourvue (VIDE > FAUX : on ne retouche jamais un site déjà
    présent). Extrait PUR, testable sans réseau (patron
    ``enrich_phones._phone_targets``)."""
    query = select(Opportunity).where(
        Opportunity.population == population,
        or_(Opportunity.website.is_(None), Opportunity.website == ""),
    )
    if source:
        query = query.where(Opportunity.source == source)
    return session.exec(query).all()[:limit]


def _result_row(opp: Opportunity, result: SiteFindResult) -> dict:
    return {
        "opp_id": result.opp_id,
        "establishment_name": opp.establishment_name,
        "city": opp.city,
        "queries": result.queries,
        "candidates": result.candidates,
        "website": result.website,
        "verdict": result.verdict,
        "name_signal": result.name_signal,
        "corroboration": result.corroboration,
        "inspected": result.inspected,
    }


def _record_stats(stats: SiteStats, verdict: str) -> None:
    if verdict == "found":
        stats.found += 1
    elif verdict == "locked_out":
        stats.locked_out += 1
    elif verdict == "no_candidate":
        stats.no_candidate += 1
    elif verdict == "search_unavailable":
        stats.search_unavailable += 1
    else:
        stats.errors += 1


def run_find_sites(
    population: str = "architecte",
    source: Optional[str] = "sirene_stock",
    limit: int = 500,
    apply: bool = False,
    out: Optional[str] = None,
    session: Optional[Session] = None,
    fetch: Optional[HtmlFetch] = None,
) -> SiteStats:
    """Passe de découverte de sites : cible les fiches sans site d'une
    population/source, tente ``find_site`` par fiche. ``--dry-run`` (défaut) :
    JSONL seulement, aucune écriture dans ``opportunities``. ``--apply`` :
    écrit ``website`` seulement sur les fiches ``found`` dont le champ était
    vide. Commit PAR FICHE, fail-soft."""
    init_db()
    own_session = session is None
    session = session or Session(engine)
    fetch = fetch or _polite_get
    stats = SiteStats(population=population, source=source or "")
    today = date.today()

    out_file: Optional[TextIO] = None
    try:
        if out:
            out_file = open(out, "w", encoding="utf-8")

        targets = _site_targets(session, population, source, limit)
        for opp in targets:
            stats.scanned += 1
            try:
                result = find_site(opp, session, fetch=fetch, today=today)
                _record_stats(stats, result.verdict)

                line = json.dumps(_result_row(opp, result), ensure_ascii=False)
                if out_file is not None:
                    out_file.write(line + "\n")
                else:
                    print(line)

                if apply and result.verdict == "found" and not opp.website:
                    opp.website = result.website
                    session.add(opp)
                session.commit()
            except Exception:
                stats.errors += 1
                session.rollback()
    finally:
        if out_file is not None:
            out_file.close()
        if own_session:
            session.close()

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Découverte de site propre (dry-run par défaut / --apply)."
    )
    parser.add_argument("--population", default="architecte", help="Population ciblée.")
    parser.add_argument("--source", default="sirene_stock",
                        help="Source ciblée (vide/'' = toutes sources de la population).")
    parser.add_argument("--limit", type=int, default=500, help="Nombre max de fiches.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true",
                      help="JSONL seulement, AUCUNE écriture dans opportunities (défaut).")
    mode.add_argument("--apply", action="store_true",
                      help="Écrit website sur les fiches 'found' (jamais d'écrasement). "
                           "Un seul écrivain SQLite à la fois : ne pas lancer en parallèle "
                           "d'une autre passe d'enrichissement.")
    parser.add_argument("--out", default=None, help="Fichier JSONL de sortie (sinon stdout).")
    args = parser.parse_args()

    source = args.source or None
    mode_label = "apply" if args.apply else "dry-run"
    print(f"Recherche de sites (population={args.population}, source={source}, mode={mode_label})...",
         file=sys.stderr)
    stats = run_find_sites(population=args.population, source=source, limit=args.limit,
                           apply=bool(args.apply), out=args.out)
    print("[OK] Termine :", file=sys.stderr)
    for key, value in asdict(stats).items():
        print(f"   {key:<14} = {value}", file=sys.stderr)


if __name__ == "__main__":
    main()
