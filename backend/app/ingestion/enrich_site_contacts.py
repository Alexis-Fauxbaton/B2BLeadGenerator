"""Récupération d'EMAIL + INSTAGRAM + FACEBOOK depuis le site du lead, pour une
population de leads (ex. architectes).

Constat du propriétaire (lead UFDI « JULIE BARBEAU », site
juliebarbeaudecoration.fr) : les leads annuaire/places ont souvent un site web
mais email et Instagram restent vides — ``website_scraper.scrape_contacts``
(home + pages contact, mailto: prioritaire anti-placeholders EMAIL_JUNK,
INSTA_RE) existe déjà mais n'était branché dans AUCUNE passe. Cette passe le
branche enfin (via ``website_scraper.scrape_site_contacts``, variante qui
ajoute la désambiguïsation multi-comptes Instagram), en réutilisant le garde
« site propre » (``own_site``, partagé avec ``enrich_phones.py``).

Doctrine VIDE > FAUX : ne remplit QUE les champs vides (email / instagram /
facebook) ; jamais d'écrasement d'un champ déjà rempli, jamais de dégradation
d'une ``contact_confidence`` existante. Le téléphone reste du ressort exclusif
d'``enrich_phones.py`` (cette passe n'y touche pas).

Usage :
    python -m app.ingestion.enrich_site_contacts --population architecte [--limit N]
"""
from __future__ import annotations

import argparse
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Optional

from sqlmodel import Session, select

from ..database import engine, init_db
from ..models import Opportunity
from .enrichment.own_site import own_site
from .enrichment.website_scraper import scrape_site_contacts

# Throttle léger entre deux sites scrapés (poli envers les sites tiers).
_THROTTLE_SECONDS = 0.5


@dataclass
class SiteContactStats:
    """Compteurs d'un run de récupération email/Instagram/Facebook depuis le
    site propre d'un lead."""
    population: str = "architecte"
    scanned: int = 0
    email_filled: int = 0
    insta_filled: int = 0
    fb_filled: int = 0
    none: int = 0
    errors: int = 0


def _needs_site_contacts(opp: Opportunity) -> bool:
    """Cible : site propre présent ET au moins un des 3 champs encore vide."""
    return bool(own_site(opp.website)) and not (opp.email and opp.instagram and opp.facebook)


def _site_contact_targets(session: Session, population: str, limit: int):
    """Fiches d'une population avec un site propre et au moins un champ contact
    (email/instagram/facebook) vide. Filtre SQL grossier (website non nul) puis
    filtre Python (garde ``own_site`` + vide sur les 3 champs) — extrait pur,
    testable sans réseau."""
    rows = session.exec(
        select(Opportunity).where(
            Opportunity.population == population,
            Opportunity.website.is_not(None),
        )
    ).all()
    return [o for o in rows if _needs_site_contacts(o)][:limit]


def _enrich_one_site_contact(opp: Opportunity, stats: SiteContactStats) -> None:
    """Scrape le site propre du lead ; ne remplit QUE les champs vides parmi
    email/instagram/facebook. Ne touche JAMAIS ``phone`` (rôle d'enrich_phones)."""
    site = own_site(opp.website)
    contacts = scrape_site_contacts(site) if site else {}

    filled_any = False

    if contacts.get("email") and not opp.email:
        opp.email = contacts["email"]
        stats.email_filled += 1
        filled_any = True

    if contacts.get("instagram") and not opp.instagram:
        opp.instagram = contacts["instagram"]
        stats.insta_filled += 1
        filled_any = True

    if contacts.get("facebook") and not opp.facebook:
        opp.facebook = contacts["facebook"]
        stats.fb_filled += 1
        filled_any = True

    if filled_any and not opp.contact_confidence:
        # Provenance = SON propre site -> haute. Ne dégrade JAMAIS une
        # confiance déjà posée par une passe antérieure (Places/OSM…).
        opp.contact_confidence = "haute"

    opp.contact_enriched_at = datetime.utcnow()  # on a tenté (même si vide)

    if not filled_any:
        stats.none += 1


def run_site_contacts_enrich(
    population: str = "architecte",
    limit: int = 500,
    session: Optional[Session] = None,
    throttle: float = _THROTTLE_SECONDS,
) -> SiteContactStats:
    """Passe email/Instagram/Facebook : cible les leads d'une population avec un
    site propre et au moins un champ vide, scrape ce site, ne remplit que les
    vides. Commit par fiche, fail-soft (une erreur sur un site n'interrompt pas
    le run)."""
    init_db()
    own_session = session is None
    session = session or Session(engine)
    stats = SiteContactStats(population=population)

    try:
        targets = _site_contact_targets(session, population, limit)
        for i, opp in enumerate(targets):
            stats.scanned += 1
            try:
                _enrich_one_site_contact(opp, stats)
                session.add(opp)
                session.commit()
            except Exception:
                stats.errors += 1
                session.rollback()
            if throttle and i < len(targets) - 1:
                time.sleep(throttle)
    finally:
        if own_session:
            session.close()

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Récupération email/Instagram/Facebook depuis le site du lead."
    )
    parser.add_argument("--population", default="architecte", help="Population ciblée.")
    parser.add_argument("--limit", type=int, default=500, help="Nombre max de fiches.")
    args = parser.parse_args()

    print(f"Recuperation contacts site (population={args.population})...")
    stats = run_site_contacts_enrich(population=args.population, limit=args.limit)
    print("[OK] Termine :")
    for key, value in asdict(stats).items():
        print(f"   {key:<14} = {value}")


if __name__ == "__main__":
    main()
