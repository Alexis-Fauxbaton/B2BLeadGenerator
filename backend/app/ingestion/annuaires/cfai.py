"""Connecteur CFAI — annuaire des architectes d'intérieur (A2, brique annuaires).

Source de STOCK qualifiée : membres du Conseil Français des Architectes d'Intérieur
= ~100 % de la cible par construction (sonde-a2.json). HTML statique pur (aucun JS,
confirmé par la sonde), pagination GET `?page=N` (15 lignes/page, 738 total).
Robots CFAI permissif. Seul bruit : les « Membres Honoraires » (retraités) —
écartés déterministement (parse_fiche -> None). Fail-soft partout."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup

from ..base import Connector, LeadCandidate
from .http import HtmlFetch, polite_get

BASE = "https://www.cfai.fr"
LIST_URL = f"{BASE}/fr/recherche/annuaire-professionnel"


def parse_list_page(html: str) -> List[Dict[str, str]]:
    """Lignes de la table annuaire -> [{fiche_id, cp, ville, nom, societe, fiche_url}].
    Ignore toute ligne sans lien fiche. PURE."""
    soup = BeautifulSoup(html or "", "html.parser")
    out: List[Dict[str, str]] = []
    table = soup.select_one("table.table-list")
    if table is None:
        return out
    for tr in table.select("tbody tr"):
        link = tr.select_one("a[href*='/adherent/']")
        if link is None:
            continue
        href = link.get("href", "")
        m = re.search(r"/adherent/(\d+)", href)
        if not m:
            continue
        tds = tr.find_all("td")

        def _txt(i: int) -> str:
            return tds[i].get_text(" ", strip=True) if i < len(tds) else ""

        out.append({
            "fiche_id": m.group(1),
            "cp": _txt(0),
            "ville": _txt(1),
            "nom": _txt(2),
            "societe": _txt(3),
            "fiche_url": href if href.startswith("http") else f"{BASE}{href}",
        })
    return out


def parse_total(html: str) -> Optional[int]:
    """Entier du badge « N résultats », ou None. PURE."""
    soup = BeautifulSoup(html or "", "html.parser")
    badge = soup.select_one("span.badge")
    if badge is None:
        return None
    m = re.search(r"(\d[\d\s]*)\s*résultats", badge.get_text(" ", strip=True))
    return int(m.group(1).replace(" ", "")) if m else None


def parse_fiche(html: str, fiche_id: str) -> Optional[Dict[str, Any]]:
    """Fiche adhérent -> dict, ou None si Membre Honoraire (garde sonde #2) ou
    <h1> absent. PURE. Extrait nom/société/activité/adresse/tél/email/site."""
    soup = BeautifulSoup(html or "", "html.parser")
    h1 = soup.select_one("h1")
    if h1 is None:
        return None
    name = h1.get_text(" ", strip=True)
    summary = soup.select_one(".member-activity-summary")
    company_el = soup.select_one(".member-company")
    activity_el = soup.select_one(".member-activity")
    # Garde honoraire (retraité) : le marqueur « Membre Honoraire » apparaît soit dans
    # .member-activity-summary (sonde adhérent 17) soit dans .member-company (adhérent
    # 81 « Retraité - Membre Honoraire », observé au run réel). On teste les deux.
    honoraire_text = " ".join(
        el.get_text(" ", strip=True).lower()
        for el in (summary, company_el) if el
    )
    if "honoraire" in honoraire_text:
        return None  # retraité : pas de valeur commerciale

    # Sections Contact : chaque <h3> titre -> le .details-group qui suit.
    def _group_after(title_kw: str) -> str:
        for h3 in soup.select("h3"):
            if title_kw in h3.get_text(" ", strip=True).lower():
                grp = h3.find_next_sibling(class_="details-group")
                if grp:
                    return grp.get_text(" ", strip=True)
        return ""

    address = _group_after("adresse")
    phone = _group_after("téléphone") or _group_after("telephone")
    mail = soup.select_one("a[href^='mailto:']")
    email = mail.get("href", "")[len("mailto:"):].strip() if mail else None
    # Site : lien externe dans la section Site (pas les réseaux du footer).
    website = None
    for h3 in soup.select("h3"):
        if h3.get_text(" ", strip=True).lower().startswith("site"):
            grp = h3.find_next_sibling(class_="details-group")
            a = grp.select_one("a[href]") if grp else None
            if a:
                website = a.get("href", "").strip()
            break

    m = re.search(r"\b(\d{5})\b", address)
    city = ""
    if m:
        city = address[m.end():].strip(" ,")

    return {
        "fiche_id": fiche_id,
        "name": name,
        "company": company_el.get_text(" ", strip=True) if company_el else "",
        "activity": activity_el.get_text(" ", strip=True) if activity_el else "",
        "address": address,
        "city": city,
        "phone": phone,
        "email": email or None,
        "website": website,
        "is_honoraire": False,
    }


class CfaiConnector(Connector):
    """Crawler CFAI : pagine la liste (GET), puis fetch chaque fiche (bornée par
    `limit`). Honoraires écartés. HTTP injectable (tests sans réseau)."""
    name = "cfai"

    def __init__(self, http_fetch: HtmlFetch = polite_get) -> None:
        self.http_fetch = http_fetch
        self.last_total_count = 0

    def fetch(self, since_days: int = 0, limit: int = 800,
              max_pages: int = 60, **_: Any) -> List[Dict[str, Any]]:
        rows: List[Dict[str, str]] = []
        for page in range(1, (max_pages or 1) + 1):
            html = self.http_fetch(f"{LIST_URL}?page={page}")
            if not html:
                break
            if page == 1:
                total = parse_total(html)
                if total is not None:
                    self.last_total_count = total
            page_rows = parse_list_page(html)
            if not page_rows:
                break  # plus de lignes : fin de pagination
            rows.extend(page_rows)
            if len(rows) >= limit:
                break
        if not self.last_total_count:
            self.last_total_count = len(rows)

        out: List[Dict[str, Any]] = []
        for row in rows[:limit]:
            html = self.http_fetch(row["fiche_url"])
            if not html:
                continue
            fiche = parse_fiche(html, row["fiche_id"])
            if fiche is None:
                continue  # honoraire ou fiche illisible
            fiche["fiche_url"] = row["fiche_url"]
            fiche.setdefault("city", row.get("ville", ""))
            out.append(fiche)
        return out

    def to_candidates(self, records: List[Dict[str, Any]]) -> List[LeadCandidate]:
        from datetime import date
        today = date.today()
        out: List[LeadCandidate] = []
        for f in records:
            company = (f.get("company") or "").strip()
            name = (f.get("name") or "").strip()
            proof = "Architecte d'intérieur membre du CFAI (annuaire professionnel)."
            out.append(LeadCandidate(
                source="annuaire",
                source_ref=f"cfai:{f['fiche_id']}",
                establishment_name=company or name,
                city=f.get("city") or "",
                address=f.get("address") or "",
                main_signal="prescripteur actif",
                secondary_signals=["annuaire cfai"],
                lifecycle_label="studio_actif",
                population="architecte",
                establishment_type="architecte d'intérieur",
                decision_maker=name or None,
                detection_date=today,
                classification_text=" ".join(filter(None, [company, name, f.get("activity")])),
                email=f.get("email"),
                website=f.get("website"),
                proof_text=proof,
                proof_url=f.get("fiche_url") or "",
                # Téléphone exposé en clair sur la fiche (« Téléphones/fax »,
                # publié par le membre) : reporté dans raw['phone'] -- seul
                # chemin lu par pipeline._process_candidate pour remplir
                # Opportunity.phone (même contrat qu'UFDI data-numero / Places
                # nationalPhoneNumber). Sans ceci le téléphone est parsé par
                # parse_fiche puis silencieusement perdu (régression 728 fiches).
                raw={"phone": f.get("phone") or None},
            ))
        return out
