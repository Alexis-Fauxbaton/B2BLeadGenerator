"""Orchestrateur d'enrichissement contact (waterfall, gratuit).

Pour un établissement : OSM (tags directs) -> si un site est trouvé, on le
scrape pour combler email / instagram / facebook / téléphone manquants.
Ne remplit que les champs vides. Fail-soft de bout en bout.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from .osm import lookup_osm
from .places import lookup_places
from .website_scraper import scrape_contacts


@dataclass
class ContactInfo:
    phone: Optional[str] = None
    email: Optional[str] = None
    website: Optional[str] = None
    instagram: Optional[str] = None
    facebook: Optional[str] = None
    review_count: Optional[int] = None  # nb d'avis Places -> proxy de fraîcheur
    match_basis: Optional[str] = None  # 'geo' | 'text' | None -> pilote la confiance

    def has_priority(self) -> bool:
        """A-t-on au moins un des champs prioritaires (email/tel/insta) ?"""
        return bool(self.email or self.phone or self.instagram)


class ContactEnricher:
    def __init__(self, osm_delay: float = 1.0):
        self.osm_delay = osm_delay  # Overpass est rate-limité : on reste poli.

    def enrich(
        self,
        name: str,
        latitude: Optional[float],
        longitude: Optional[float],
        website: Optional[str] = None,
        city: Optional[str] = None,
        postal: Optional[str] = None,
    ) -> ContactInfo:
        info = ContactInfo(website=website or None)

        # 0. Google Places (si clé) — tête de waterfall : tel + site, bonne
        #    couverture des lieux physiques. Match validé en amont (places.py) :
        #    par distance au point Sirene (lat/lon) si dispo, sinon par texte.
        places = lookup_places(name, city=city, postal=postal, lat=latitude, lon=longitude)
        if places.get("matched"):
            info.phone = info.phone or places.get("phone")
            info.website = info.website or places.get("website")
            info.review_count = places.get("review_count")
            info.match_basis = places.get("match_basis")

        # 1. OSM (tags directs) si on a des coordonnées.
        if latitude is not None and longitude is not None:
            osm = lookup_osm(name, latitude, longitude)
            info.phone = info.phone or osm.get("phone")
            info.website = info.website or osm.get("website")
            info.instagram = info.instagram or osm.get("instagram")
            info.email = info.email or osm.get("email")
            info.facebook = info.facebook or osm.get("facebook")
            time.sleep(self.osm_delay)

        # 2. Scrape du site (le pilier pour email / Instagram).
        if info.website and not (info.email and info.instagram and info.phone):
            scraped = scrape_contacts(info.website)
            info.email = info.email or scraped.get("email")
            info.instagram = info.instagram or scraped.get("instagram")
            info.facebook = info.facebook or scraped.get("facebook")
            info.phone = info.phone or scraped.get("phone")

        return info
