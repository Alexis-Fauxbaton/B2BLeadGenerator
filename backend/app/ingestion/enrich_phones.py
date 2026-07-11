"""Récupération de TÉLÉPHONES pour une population de leads (ex. architectes).

Le propriétaire engage des closers qui vont APPELER : un faux numéro = un appel
gênant au mauvais commerce. Doctrine VIDE > FAUX, verrous d'identité NON
négociables. On ne remplit QUE les fiches sans téléphone (jamais d'écrasement).

Waterfall par confiance DÉCROISSANTE :
  1. SITE DU LEAD  — home + pages contact/mentions ; liens ``tel:`` puis regex
     FR, désambiguïsation par palier (``website_scraper.scrape_phone``).
     Confiance « haute » : c'est SON site.
  2. GOOGLE PLACES / OSM — via ``ContactEnricher`` existant : ses verrous
     d'identité (géo-confirmé + concordance de nom, sinon concordance de nom
     FORTE) sont réutilisés tels quels, PAS réinventés. Confiance selon le
     ``match_basis`` renvoyé (« haute » si géo-confirmé, sinon « basse »).
  3. TÉLÉPHONE APIFY — champ « business phone » d'un cache profil, s'il existe.
     Aucun champ de ce type n'a été observé dans les payloads Apify de ce
     codebase : stub fail-soft (renvoie None), présent pour compléter la
     cascade et rester extensible.

Réutilise les colonnes ``phone`` / ``contact_confidence`` / ``contact_enriched_at``
(AUCUNE colonne ajoutée). Enrichisseur fail-soft, jamais bloquant.

Usage :
    python -m app.ingestion.enrich_phones --population architecte [--limit N]
"""
from __future__ import annotations

import argparse
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Optional, Tuple

from sqlmodel import Session, select

from ..database import engine, init_db
from ..models import Opportunity
from ..services.contact_quality import establishment_confidence
from .enrichment import siret_matcher
from .enrichment.contact_enricher import ContactEnricher
from .enrichment.own_site import own_site as _own_site
from .enrichment.sirene import SireneEnricher
from .enrichment.website_scraper import normalize_fr_phone, scrape_phone


@dataclass
class PhoneStats:
    """Compteurs d'un run de récupération de téléphones."""
    population: str = "architecte"
    scanned: int = 0
    with_phone: int = 0      # numéro écrit (fiche jusque-là sans téléphone)
    high_conf: int = 0       # dont confiance « haute » (site du lead / géo / apify)
    low_conf: int = 0        # dont confiance « basse » (Places nom-fort non géo)
    none: int = 0            # aucun numéro sûr
    errors: int = 0


def _confidence_for(basis: Optional[str]) -> str:
    """Confiance du téléphone selon sa provenance. « site » (son propre site) et
    « apify » (le compte lui-même) sont des sources directes -> « haute ». Pour
    Places/OSM, on délègue à ``establishment_confidence`` (« haute » si
    géo-confirmé, « basse » sinon) — même sémantique que la passe contact."""
    if basis in ("site", "apify"):
        return "haute"
    return establishment_confidence(basis)


def _phone_from_places(
    opp: Opportunity, enricher: ContactEnricher, sirene: SireneEnricher
) -> Tuple[Optional[str], Optional[str]]:
    """Téléphone via Places/OSM en RÉUTILISANT les verrous de ``ContactEnricher``.

    Géocode l'adresse à la volée (BAN, comme la passe contact) pour donner sa
    chance au verrou géo des 12 fiches architectes qui ont une adresse ; les
    autres restent en repli texte où seule une concordance de nom FORTE laisse
    passer un numéro. On passe ``website=None`` : le site du lead a DÉJÀ été
    scrapé au palier 1 avec notre désambiguïsation ; on n'autorise pas le scrape
    plat interne de ``enrich`` à réintroduire un numéro ambigu qu'on a écarté."""
    lat, lon = opp.latitude, opp.longitude
    if (lat is None or lon is None) and opp.siren:
        data = sirene.lookup(opp.siren)  # fail-soft
        if data:
            siege = data.get("siege") or {}
            lat = _coord(siege.get("latitude"))
            lon = _coord(siege.get("longitude"))
    if (lat is None or lon is None) and opp.address:
        coords = siret_matcher.geocode(opp.address, siret_matcher._http_get)
        if coords:
            lat, lon = coords
    if lat is not None and lon is not None:
        opp.latitude, opp.longitude = lat, lon  # mémorise le géocodage

    postal = None
    m = re.search(r"\b\d{5}\b", opp.address or "")
    if m:
        postal = m.group(0)

    info = enricher.enrich(
        opp.establishment_name, lat, lon, website=None, city=opp.city, postal=postal,
        main_signal=opp.main_signal, activity_start_date=opp.activity_start_date,
    )
    if info.phone:
        return normalize_fr_phone(info.phone) or info.phone, info.match_basis
    return None, None


def _phone_from_apify_cache(opp: Opportunity) -> Optional[str]:
    """Stub fail-soft : aucun champ « business phone » n'existe dans les payloads
    Apify de ce codebase (profile scraper -> businessAddress / businessCategory /
    isBusinessAccount uniquement, jamais un téléphone). Renvoie None ; point
    d'extension si un tel champ apparaissait un jour dans un cache profil."""
    return None


def _enrich_one_phone(
    opp: Opportunity, enricher: ContactEnricher, sirene: SireneEnricher, stats: PhoneStats
) -> None:
    """Waterfall pour une fiche. Ne remplit ``phone`` que s'il était vide."""
    phone: Optional[str] = None
    basis: Optional[str] = None

    # 1. Site du lead (confiance haute) — uniquement si VRAI site propre.
    site = _own_site(opp.website)
    if site:
        phone = scrape_phone(site)
        if phone:
            basis = "site"

    # 2. Google Places / OSM (verrous d'identité réutilisés).
    if not phone:
        phone, basis = _phone_from_places(opp, enricher, sirene)

    # 3. Téléphone business Apify (cache) — aucun à ce jour.
    if not phone:
        phone = _phone_from_apify_cache(opp)
        if phone:
            basis = "apify"

    opp.contact_enriched_at = datetime.utcnow()  # on a tenté (même si vide)

    if phone and not opp.phone:
        opp.phone = phone
        opp.contact_confidence = _confidence_for(basis)
        stats.with_phone += 1
        if opp.contact_confidence == "haute":
            stats.high_conf += 1
        else:
            stats.low_conf += 1
    else:
        stats.none += 1


def _phone_targets(session: Session, population: str, limit: int):
    """Fiches d'une population SANS téléphone (VIDE > FAUX : on ne retouche
    jamais un numéro déjà présent). Extrait pur, testable sans réseau."""
    return session.exec(
        select(Opportunity).where(
            Opportunity.population == population,
            Opportunity.phone.is_(None),
        )
    ).all()[:limit]


def run_phone_enrich(
    population: str = "architecte",
    limit: int = 500,
    session: Optional[Session] = None,
) -> PhoneStats:
    """Passe téléphones : cible les leads d'une population sans numéro et tente
    le waterfall (site -> Places/OSM -> apify). Commit par fiche, fail-soft."""
    init_db()
    own_session = session is None
    session = session or Session(engine)
    stats = PhoneStats(population=population)
    enricher = ContactEnricher()
    sirene = SireneEnricher()

    try:
        for opp in _phone_targets(session, population, limit):
            stats.scanned += 1
            try:
                _enrich_one_phone(opp, enricher, sirene, stats)
                session.add(opp)
                session.commit()
            except Exception:
                stats.errors += 1
                session.rollback()
    finally:
        if own_session:
            session.close()

    return stats


def _coord(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Récupération de téléphones (waterfall site -> Places -> apify)."
    )
    parser.add_argument("--population", default="architecte", help="Population ciblée.")
    parser.add_argument("--limit", type=int, default=500, help="Nombre max de fiches.")
    args = parser.parse_args()

    print(f"Recuperation telephones (population={args.population})...")
    stats = run_phone_enrich(population=args.population, limit=args.limit)
    print("[OK] Termine :")
    for key, value in asdict(stats).items():
        print(f"   {key:<14} = {value}")


if __name__ == "__main__":
    main()
