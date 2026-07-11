"""Connecteur UFDI — annuaire des décorateurs/architectes d'intérieur (A2).

Source de STOCK : membres de l'Union Francophone des Décorateurs d'Intérieur.
WordPress/Divi statique (aucun JS, sonde). Découverte via la page nationale
`/decorateur/decorateurs-france-fr.html` (~157 profils réels en un fetch ; les
~98 liens de navigation départementale de la page NE SONT PAS des cartes
team_member et sont exclus par le scope `div.et_pb_team_member`) ou les 15
pages régionales (repli). Robots UFDI : `/decorateur/*.html` ALLOW (on n'utilise
QUE ce chemin ; `/membres.php` DISALLOW n'est jamais touché). Signal hospitality
NATIF (`<li>Décoration Hôtels/Restaurants</li>`) -> tier T2. Pas d'email en clair
(-> enrichissement contact aval). Téléphone en clair via `data-numero`."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup

from ..base import Connector, LeadCandidate
from .http import HtmlFetch, polite_get

BASE = "https://www.ufdi.fr"
FRANCE_URL = f"{BASE}/decorateur/decorateurs-france-fr.html"
REGION_SLUGS = [
    "auvergne-rhone-alpes", "bourgogne-franche-comte", "bretagne",
    "centre-val-de-loire", "champagne-ardennes", "corse", "cote-d-azur",
    "grand-est", "hauts-de-france", "ile-de-france", "normandie",
    "nouvelle-aquitaine", "occitanie-est", "occitanie-ouest",
    "pays-de-la-loire", "provence",
]
REGION_URLS = [f"{BASE}/decorateur/decorateurs-region-{s}.html" for s in REGION_SLUGS]

# Comptes Instagram officiels UFDI (jamais le studio lui-même).
_UFDI_IG = {"ufdideco", "ufdidecoarchi"}
_PROFILE_RE = re.compile(r"/decorateur/([a-z0-9-]+-\d+)\.html")
_HOSPITALITY = ("Décoration Hôtels", "Décoration Restaurants")


def parse_list_page(html: str) -> List[Dict[str, str]]:
    """Cartes team_member -> [{name, societe, ville, profile_url, slug}]. PURE."""
    soup = BeautifulSoup(html or "", "html.parser")
    out: List[Dict[str, str]] = []
    seen = set()
    for card in soup.select("div.et_pb_team_member"):
        a = card.select_one("h4 a[href*='/decorateur/']") or card.select_one(
            "a[href*='/decorateur/']")
        if a is None:
            continue
        href = a.get("href", "")
        m = _PROFILE_RE.search(href)
        if not m or m.group(1) in seen:
            continue
        seen.add(m.group(1))
        h5 = card.select_one("h5")
        h6 = card.select_one("h6")
        out.append({
            "name": a.get_text(" ", strip=True),
            "societe": h5.get_text(" ", strip=True) if h5 else "",
            "ville": h6.get_text(" ", strip=True) if h6 else "",
            "slug": m.group(1),
            "profile_url": href if href.startswith("http") else f"{BASE}{href}",
        })
    return out


def parse_profile(html: str) -> Dict[str, Any]:
    """Fiche décorateur -> {name, city, phone, website, instagram, activities,
    hospitality}. PURE. Téléphone via data-numero (aucun JS, sonde)."""
    soup = BeautifulSoup(html or "", "html.parser")
    title = soup.select_one("title")
    title_txt = title.get_text(" ", strip=True) if title else ""
    name = title_txt.split("•")[0].split("•")[0].strip()
    subhead = soup.select_one(".et_pb_fullwidth_header_subhead")
    city = subhead.get_text(" ", strip=True) if subhead else ""

    numero = soup.select_one("[data-numero]")
    phone = numero.get("data-numero", "").strip() if numero else None

    site = soup.select_one("a.site[href]")
    website = site.get("href", "").strip() if site else None

    instagram = None
    for a in soup.select("a[href*='instagram.com']"):
        m = re.search(r"instagram\.com/([A-Za-z0-9_.]+)", a.get("href", ""))
        if m and m.group(1).lower() not in _UFDI_IG:
            instagram = m.group(1)
            break

    activities = [li.get_text(" ", strip=True) for li in soup.select("li")
                  if li.get_text(" ", strip=True).startswith("Décoration")]
    hospitality = any(h in activities for h in _HOSPITALITY)

    return {"name": name, "city": city, "phone": phone or None, "website": website,
            "instagram": instagram, "activities": activities, "hospitality": hospitality}


class UfdiConnector(Connector):
    """Crawler UFDI : page nationale (repli régions), puis fetch chaque profil."""
    name = "ufdi"

    def __init__(self, http_fetch: HtmlFetch = polite_get) -> None:
        self.http_fetch = http_fetch
        self.last_total_count = 0

    def _discover(self) -> List[Dict[str, str]]:
        html = self.http_fetch(FRANCE_URL)
        rows = parse_list_page(html) if html else []
        if rows:
            return rows
        # Repli : agréger les pages régionales (dédup par slug via parse_list_page).
        seen: Dict[str, Dict[str, str]] = {}
        for url in REGION_URLS:
            h = self.http_fetch(url)
            for r in (parse_list_page(h) if h else []):
                seen.setdefault(r["slug"], r)
        return list(seen.values())

    def fetch(self, limit: int = 300, **_: Any) -> List[Dict[str, Any]]:
        rows = self._discover()
        self.last_total_count = len(rows)
        out: List[Dict[str, Any]] = []
        for row in rows[:limit]:
            html = self.http_fetch(row["profile_url"])
            prof = parse_profile(html) if html else {}
            merged = dict(row)
            merged.update({
                "name": prof.get("name") or row.get("name") or "",
                # La carte (h6) porte la COMMUNE précise ; le sous-titre du profil
                # (.et_pb_fullwidth_header_subhead) est parfois un DÉPARTEMENT
                # (ex. « Hauts-de-Seine », sonde) -> on préfère la ville de la carte
                # pour ne pas dégrader la cohérence géo du matcher/dédup.
                "city": row.get("ville") or prof.get("city") or "",
                "department": prof.get("city") or "",
                "phone": prof.get("phone"),
                "website": prof.get("website"),
                "instagram": prof.get("instagram"),
                "hospitality": bool(prof.get("hospitality")),
                "activities": prof.get("activities") or [],
            })
            out.append(merged)
        return out

    def to_candidates(self, records: List[Dict[str, Any]]) -> List[LeadCandidate]:
        from datetime import date
        today = date.today()
        out: List[LeadCandidate] = []
        for r in records:
            societe = (r.get("societe") or "").strip()
            name = (r.get("name") or "").strip()
            secondary = ["annuaire ufdi"]
            if r.get("hospitality"):
                secondary.append("portfolio hospitality/CHR")  # tier T2 (sonde #5)
            proof = "Décorateur/architecte d'intérieur membre de l'UFDI (annuaire)."
            if r.get("hospitality"):
                proof += " Fiche UFDI : Décoration Hôtels/Restaurants (signal CHR)."
            out.append(LeadCandidate(
                source="annuaire",
                source_ref=f"ufdi:{r['slug']}",
                establishment_name=societe or name,
                city=r.get("city") or "",
                main_signal="prescripteur actif",
                secondary_signals=secondary,
                lifecycle_label="studio_actif",
                population="architecte",
                establishment_type="architecte d'intérieur",
                decision_maker=name or None,
                instagram=r.get("instagram"),
                website=r.get("website"),
                email=None,  # UFDI : pas d'email en clair (sonde)
                detection_date=today,
                classification_text=" ".join(filter(None, [societe, name])),
                proof_text=proof,
                proof_url=r.get("profile_url") or "",
                raw={"phone": r.get("phone"), "activities": r.get("activities") or []},
            ))
        return out
