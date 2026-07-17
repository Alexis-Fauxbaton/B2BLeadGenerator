"""Connecteur Mon Architecte d'Intérieur (mon-architecte-interieur.com/annuaire) — A2.

WordPress + plugin Business Directory (wpbdp) : HTML statique pur (aucun JS,
confirmé sonde), une seule catégorie « Architecte » (badge « (24) » sur la page
1, ~22 fiches réellement paginées sur 3 pages -- `/annuaire/` puis
`/annuaire/page/N/`). robots.txt permissif sur `/annuaire/` (seuls
`/wp-admin/`, `/contact/`, `/mentions-legales/`, `/ajouter/` sont interdits).

Crawl en 2 temps comme CFAI/UFDI : liste (découverte id + URL fiche) puis fetch
de chaque fiche, où les champs `wpbdp-field-<slug>` (adresse/numéro de
téléphone/ville/site web) sont exposés de façon identique et fiable (même
structure DOM sur les ~22 fiches sondées). Téléphone normalisé en 5 groupes de
2 (`normalize_phone_fr`, même contrat qu'annuaire_decoration.py). Pas d'email
en clair (sonde).

Garde hors-cible : ce répertoire référence aussi des architectes BELGES (ex.
ARCHI-IN à Andenne, CREEL à Namur, Benoit Custers à Liège) -- le site ne cible
que France + Monaco (sonde). Détection déterministe dans `parse_fiche` :
mention explicite « Belgique »/« Belgium » dans l'adresse, OU code postal à 4
chiffres (les codes FR/Monaco en comptent 5, ex. 98000 Monaco). VIDE > FAUX :
adresse absente ou sans code postal identifiable -> jamais exclue (cf. fiche
Emilie Bouaziz, sonde, adresse vide, gardée).

Fail-soft partout, HTTP injectable (tests sans réseau, snapshots dans
tests/fixtures/mon_architecte_interieur/, récupérés poliment le 2026-07-17,
throttle >= 2,5 s)."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup

from ..base import Connector, LeadCandidate
from .http import HtmlFetch, polite_get

BASE = "https://www.mon-architecte-interieur.com"
LIST_URL = f"{BASE}/annuaire/"

_LISTING_ID_RE = re.compile(r"^wpbdp-listing-(\d+)$")
_POSTAL_RE = re.compile(r"\b(\d{4,5})\b")
_BELGIAN_HINT_RE = re.compile(r"\bbelgi(?:um|que)\b", re.IGNORECASE)
_PHONE_DIGITS_RE = re.compile(r"\d")


def _list_url(page: int) -> str:
    """URL de la page N de l'annuaire (page 1 = index sans suffixe). PURE."""
    if page <= 1:
        return LIST_URL
    return f"{BASE}/annuaire/page/{page}/"


def parse_list_page(html: str) -> List[Dict[str, str]]:
    """Cartes annuaire -> [{listing_id, title, listing_url}]. Ignore toute
    carte sans lien titre. PURE."""
    soup = BeautifulSoup(html or "", "html.parser")
    out: List[Dict[str, str]] = []
    seen: set = set()
    for card in soup.select("div[id^='wpbdp-listing-']"):
        m = _LISTING_ID_RE.match(card.get("id", ""))
        if not m or m.group(1) in seen:
            continue
        a = card.select_one(".listing-title a[href]")
        if a is None:
            continue
        seen.add(m.group(1))
        out.append({
            "listing_id": m.group(1),
            "title": a.get_text(" ", strip=True),
            "listing_url": a.get("href", "").strip(),
        })
    return out


def parse_total(html: str) -> Optional[int]:
    """Entier du badge « (N) » de la catégorie Architecte, ou None. PURE."""
    soup = BeautifulSoup(html or "", "html.parser")
    li = soup.select_one("#wpbdp-categories li.cat-item")
    if li is None:
        return None
    m = re.search(r"\((\d+)\)", li.get_text(" ", strip=True))
    return int(m.group(1)) if m else None


def _field_value(soup: BeautifulSoup, slug: str) -> str:
    """Texte du champ `wpbdp-field-<slug>` (bloc `.value`), ou "" si absent
    (champ non renseigné sur cette fiche -- VIDE > FAUX). PURE."""
    node = soup.select_one(f".wpbdp-field-{slug} .value")
    return node.get_text(" ", strip=True) if node else ""


def _field_link(soup: BeautifulSoup, slug: str) -> Optional[str]:
    """URL réelle (attribut href, PAS le texte parfois promotionnel) du champ
    `wpbdp-field-<slug>`, ou None si absent. PURE."""
    node = soup.select_one(f".wpbdp-field-{slug} .value a[href]")
    if node is None:
        return None
    href = node.get("href", "").strip()
    return href or None


def _is_belgian_address(address: str) -> bool:
    """Vrai si l'adresse pointe vers la Belgique (garde hors-cible : le site
    référence aussi des architectes belges, hors scope France+Monaco -- sonde).
    Déterministe : mention explicite « Belgique »/« Belgium », OU code postal à
    4 chiffres (les codes FR/Monaco en comptent 5). VIDE > FAUX : adresse
    absente ou sans code postal identifiable = jamais exclue. PURE."""
    addr = address or ""
    if _BELGIAN_HINT_RE.search(addr):
        return True
    # Code postal FR/Monaco = 5 chiffres, belge = 4. Un numéro de voie/route à
    # 4 chiffres en tête (« 1234 Route de Grasse, 06140 VENCE ») ne doit PAS
    # déclencher la garde : on n'exclut que si un token à 4 chiffres existe SANS
    # aucun token à 5 (une adresse FR porte toujours son CP à 5 chiffres). Sinon
    # `search` attraperait ce numéro de voie et écarterait un vrai lead FR.
    nums = _POSTAL_RE.findall(addr)
    return any(len(n) == 4 for n in nums) and not any(len(n) == 5 for n in nums)


def normalize_phone_fr(raw: Optional[str]) -> Optional[str]:
    """Téléphone FR normalisé en 5 groupes de 2 chiffres (« 01 23 45 67 89 »).
    Accepte les formats bruts (points, espaces, +33...). None si absent ou non
    reconnaissable comme un numéro FR à 10 chiffres (VIDE > FAUX -- couvre
    aussi, en pratique, les numéros belges à 9 chiffres qui échapperaient à la
    garde adresse). PURE."""
    if not raw:
        return None
    digits = "".join(_PHONE_DIGITS_RE.findall(raw))
    if digits.startswith("33") and len(digits) == 11:
        digits = "0" + digits[2:]
    if len(digits) != 10 or not digits.startswith("0"):
        return None
    return " ".join(digits[i:i + 2] for i in range(0, 10, 2))


def parse_fiche(html: str, listing_id: str) -> Optional[Dict[str, Any]]:
    """Fiche architecte -> dict, ou None si <h1> absent (fiche illisible) ou
    adresse belge (garde hors-cible). PURE. Extrait nom/adresse/ville/
    téléphone/site depuis les champs `wpbdp-field-*` (structure identique sur
    toutes les fiches sondées)."""
    soup = BeautifulSoup(html or "", "html.parser")
    h1 = soup.select_one("h1")
    if h1 is None:
        return None
    address = _field_value(soup, "adresse")
    if _is_belgian_address(address):
        return None  # hors-cible : architecte belge (site France + Monaco)

    return {
        "listing_id": listing_id,
        "name": h1.get_text(" ", strip=True),
        "address": address,
        "city": _field_value(soup, "ville"),
        "phone": normalize_phone_fr(_field_value(soup, "numero_de_telephone_")),
        "website": _field_link(soup, "site_web"),
    }


class MonArchitecteInterieurConnector(Connector):
    """Crawler Mon Architecte d'Intérieur : pagine la liste (GET), puis fetch
    chaque fiche (bornée par `limit`). Fiches belges écartées. HTTP injectable
    (tests sans réseau)."""
    name = "monarchitecteinterieur"

    def __init__(self, http_fetch: HtmlFetch = polite_get) -> None:
        self.http_fetch = http_fetch
        self.last_total_count = 0

    def fetch(self, since_days: int = 0, limit: int = 200,
              max_pages: int = 20, **_: Any) -> List[Dict[str, Any]]:
        rows: List[Dict[str, str]] = []
        for page in range(1, (max_pages or 1) + 1):
            html = self.http_fetch(_list_url(page))
            if not html:
                break
            if page == 1:
                total = parse_total(html)
                if total is not None:
                    self.last_total_count = total
            page_rows = parse_list_page(html)
            if not page_rows:
                break  # plus de cartes : fin de pagination
            rows.extend(page_rows)
            if len(rows) >= limit:
                break
        if not self.last_total_count:
            self.last_total_count = len(rows)

        out: List[Dict[str, Any]] = []
        for row in rows[:limit]:
            html = self.http_fetch(row["listing_url"])
            if not html:
                continue
            fiche = parse_fiche(html, row["listing_id"])
            if fiche is None:
                continue  # hors-cible (Belgique) ou fiche illisible
            fiche["fiche_url"] = row["listing_url"]
            out.append(fiche)
        return out

    def to_candidates(self, records: List[Dict[str, Any]]) -> List[LeadCandidate]:
        from datetime import date
        today = date.today()
        out: List[LeadCandidate] = []
        for r in records:
            name = (r.get("name") or "").strip()
            proof = "Architecte référencé dans l'annuaire mon-architecte-interieur.com."
            out.append(LeadCandidate(
                source="annuaire",
                source_ref=f"monarchitecteinterieur:{r['listing_id']}",
                establishment_name=name,
                city=r.get("city") or "",
                address=r.get("address") or "",
                main_signal="prescripteur actif",
                secondary_signals=["annuaire monarchitecteinterieur"],
                lifecycle_label="studio_actif",
                population="architecte",
                establishment_type="architecte d'intérieur",
                decision_maker=None,
                detection_date=today,
                classification_text=name,
                email=None,  # pas d'email en clair sur ce répertoire (sonde)
                website=r.get("website"),
                proof_text=proof,
                proof_url=r.get("fiche_url") or "",
                # Téléphone normalisé reporté dans raw['phone'] -- seul chemin lu
                # par pipeline._process_candidate pour remplir Opportunity.phone
                # (même contrat que CFAI/UFDI/Annuaire Décoration/Places).
                raw={"phone": r.get("phone") or None},
            ))
        return out
