"""Enrichissement des DIRIGEANTS (personnes physiques) pour une population de
leads (ex. architectes issus du STOCK Sirene, B1 volume max).

Contexte : les fiches `source='sirene_stock'` portent un SIREN (natif, issu du
flux stock INSEE) mais AUCUN dirigeant — l'API stock INSEE (`insee.py`) ne
livre que le nom du représentant légal principal (`decision_maker`, déjà
rempli par `sirene_stock.py`), jamais la liste complète du RNE. Or
`site_finder._guess_domains` utilise le nom COMPLET (prénom + nom) du premier
dirigeant pour deviner le domaine du site (cas prouvé : « CAT LASSALLE
INTERIEURS », dirigeante Catherine Lassalle -> catherinelassalle.fr) — un
ressort perdu tant que `dirigeants` reste vide.

Cette passe complète `dirigeants` (colonne JSON, `List[str]`) depuis l'API
PUBLIQUE recherche-entreprises.api.gouv.fr (DINUM, gratuite, sans clé) en
réutilisant `SireneEnricher` (`enrichment/sirene.py`) — même client HTTP, même
mécanisme de throttle/cache, paramétré ici à 0,5 s/appel (politesse ; l'API
tolère ~7 req/s).

Format ÉCRIT (même convention que les autres connecteurs qui remplissent
`dirigeants` — cf. `bodacc._format_dirigeant` — et que `decision_maker` dans
`sirene_stock.map_stock_etablissement`, qui applique déjà `.title()` aux
prénom/nom bruts EN CAPITALES du registre) : ``"Prénom Nom"`` ou, si la
qualité est connue, ``"Prénom Nom, Qualité"``. `site_finder._dirigeant_full_name`
consomme ce format en coupant sur la première virgule — compatible avec les
deux variantes.

Doctrine **VIDE > FAUX** : SIREN inconnu de l'API, réponse ne portant AUCUN
dirigeant personne physique (que des personnes morales/holdings — ignorées),
ou réponse dont le SIREN ne correspond PAS à celui demandé (repli fuzzy de
l'API sur une recherche texte) -> on n'écrit RIEN. Fail-soft partout : une
fiche en erreur ne bloque jamais le run (skip, `errors` incrémenté), commit
PAR FICHE (une seule base, un seul écrivain à la fois).

Usage :
    python -m app.ingestion.enrich_dirigeants --population architecte \\
        --source sirene_stock --limit 500 [--dry-run]
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

from sqlmodel import Session, select

from ..database import engine, init_db
from ..models import Opportunity
from .enrichment.sirene import SireneEnricher

# Throttle dédié à cette passe : plus poli que le défaut de SireneEnricher
# (0,15 s), l'API publique tolérant ~7 req/s mais restant partagée.
RATE_DELAY = 0.5


@dataclass
class DirigeantsStats:
    """Compteurs d'un run d'enrichissement des dirigeants."""
    population: str = "architecte"
    source: str = "sirene_stock"
    scanned: int = 0
    enriched: int = 0     # dirigeants PP écrits (>= 1)
    no_person: int = 0    # SIREN inconnu / aucun PP / SIREN ne correspond pas
    errors: int = 0


def _format_dirigeant(d: Dict[str, Any]) -> Optional[str]:
    """Un dirigeant de la charge utile recherche-entreprises -> ``"Prénom
    Nom"`` (+ ``", Qualité"`` si connue), ou ``None`` si prénom OU nom manque
    (personnes morales : pas de ``prenoms``/``nom``, seulement ``denomination``
    — VIDE > FAUX, jamais de nom partiel). Fonction PURE."""
    prenom = (d.get("prenoms") or d.get("prenom") or "").strip()
    nom = (d.get("nom") or "").strip()
    if not prenom or not nom:
        return None
    full = f"{prenom.title()} {nom.title()}"
    qualite = (d.get("qualite") or "").strip()
    return f"{full}, {qualite}" if qualite else full


def extract_dirigeants_pp(data: Dict[str, Any]) -> List[str]:
    """Dirigeants PERSONNES PHYSIQUES (prénom + nom), dans l'ordre déclaré par
    l'API, d'une charge utile recherche-entreprises. Ignore les personnes
    morales (holdings, commissaires aux comptes société — `type_dirigeant`
    != "personne physique"). Rend ``[]`` si aucun PP nommé. Fonction PURE,
    testable sans réseau."""
    out: List[str] = []
    for d in (data.get("dirigeants") or []):
        if d.get("type_dirigeant") != "personne physique":
            continue
        formatted = _format_dirigeant(d)
        if formatted:
            out.append(formatted)
    return out


def _targets(session: Session, population: str, source: str, limit: int):
    """Fiches d'une population/source AVEC siren et SANS dirigeant connu
    (VIDE/NULL — jamais de ré-écriture d'une fiche déjà remplie). Le filtre
    « dirigeants vide » se fait côté Python (colonne JSON, portable
    sqlite/postgres, même approche que `enrich_phones._phone_targets`).
    Extrait pur, testable sans réseau."""
    query = select(Opportunity).where(
        Opportunity.population == population,
        Opportunity.source == source,
        Opportunity.siren.is_not(None),
        Opportunity.siren != "",
    )
    rows = session.exec(query).all()
    return [o for o in rows if not o.dirigeants][:limit]


def _enrich_one(
    opp: Opportunity, sirene: Any, stats: DirigeantsStats, dry_run: bool = False
) -> None:
    """Enrichit UNE fiche : lookup SIREN, garde le SIREN retourné identique à
    celui demandé (VIDE > FAUX face au repli fuzzy de l'API), extrait les PP,
    écrit `dirigeants` seulement si non-vide et pas en `--dry-run`. Propage
    toute exception réseau (le run l'attrape autour de cet appel — fail-soft
    au niveau de la FICHE, jamais silencieux au niveau de la fonction pure)."""
    data = sirene.lookup(opp.siren)
    if not data or str(data.get("siren")) != str(opp.siren):
        stats.no_person += 1
        return

    dirigeants = extract_dirigeants_pp(data)
    if not dirigeants:
        stats.no_person += 1
        return

    if not dry_run:
        opp.dirigeants = dirigeants
    stats.enriched += 1


def run_dirigeants_enrich(
    population: str = "architecte",
    source: str = "sirene_stock",
    limit: int = 500,
    session: Optional[Session] = None,
    dry_run: bool = False,
) -> DirigeantsStats:
    """Passe complète : cible les fiches sans dirigeant, interroge l'API
    publique (throttlée), écrit `dirigeants` fiche par fiche, commit PAR
    FICHE. Fail-soft : une erreur sur une fiche est comptée et n'interrompt
    jamais le run."""
    init_db()
    own_session = session is None
    session = session or Session(engine)
    stats = DirigeantsStats(population=population, source=source)
    sirene = SireneEnricher(rate_delay=RATE_DELAY)

    try:
        for opp in _targets(session, population, source, limit):
            stats.scanned += 1
            try:
                _enrich_one(opp, sirene, stats, dry_run=dry_run)
                if not dry_run:
                    session.add(opp)
                    session.commit()
                else:
                    session.rollback()
            except Exception:
                stats.errors += 1
                session.rollback()
    finally:
        if own_session:
            session.close()

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Enrichissement des dirigeants (personnes physiques) "
                    "via recherche-entreprises.api.gouv.fr."
    )
    parser.add_argument("--population", default="architecte", help="Population ciblée.")
    parser.add_argument("--source", default="sirene_stock", help="Source ciblée.")
    parser.add_argument("--limit", type=int, default=500, help="Nombre max de fiches.")
    parser.add_argument("--dry-run", action="store_true",
                        help="N'écrit rien en base, affiche seulement les stats.")
    args = parser.parse_args()

    print(f"Enrichissement dirigeants (population={args.population}, "
          f"source={args.source}, dry_run={args.dry_run})...")
    stats = run_dirigeants_enrich(
        population=args.population, source=args.source, limit=args.limit,
        dry_run=args.dry_run,
    )
    print("[OK] Termine :")
    for key, value in asdict(stats).items():
        print(f"   {key:<14} = {value}")


if __name__ == "__main__":
    main()
