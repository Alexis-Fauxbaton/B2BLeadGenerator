"""Connecteur Annuaire Décoration (annuairedecoration.fr) — A2, annuaires.

Répertoire GÉNÉRALISTE déco (623 fiches, toutes catégories confondues) : on ne
crawle QUE les 2 catégories « intérieur pur » (~200 fiches, sonde) —
`architecte-d-interieur` et `decorateur-d-interieur`. HTML statique pur
(moteur Arfooo Annuaire, aucun JS, confirmé sonde), pagination par URL dédiée
(`/{categorie}/` page 1, `/{categorie}-p{N}/` pages suivantes, 5 pages/catégorie,
~20 fiches/page). robots.txt permissif sur ces chemins.

Deux gardes hors-cible déterministes (repérées en sonde/exploration réelle) :
  - CROSS-CATÉGORIE : le moteur Arfooo injecte parfois, au milieu d'une liste
    de catégorie, une fiche appartenant à une AUTRE catégorie (ex. une fiche
    `/coach-decoration/...` observée dans la liste `decorateur-d-interieur`)
    -> écartée par préfixe d'URL (`parse_list_page`).
  - HORS FRANCE : annuaire généraliste, certaines fiches déclarent un pays
    étranger (champ « Pays ») -> écartées si renseigné et != France
    (`parse_fiche`). VIDE > FAUX : champ absent = conservé.

Téléphone (champ « Téléphone », chiffres bruts) normalisé en 5 groupes de 2
(`normalize_phone_fr`). Pas d'email en clair (aucun mailto observé). Fail-soft
partout, HTTP injectable (tests sans réseau)."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup, Tag

from ..base import Connector, LeadCandidate
from .http import HtmlFetch, polite_get

BASE = "https://annuairedecoration.fr"

# Les 2 seules catégories « intérieur pur » du répertoire (sur 19 catégories
# déco au total) — cf. sonde a2 (volume_estime).
CATEGORIES: Dict[str, str] = {
    "architecte-d-interieur": "architecte d'intérieur",
    "decorateur-d-interieur": "décorateur d'intérieur",
}

_FICHE_ID_RE = re.compile(r"-s(\d+)\.html$")
_MAX_PAGE_RE = re.compile(r"-p(\d+)/")
_PHONE_DIGITS_RE = re.compile(r"\d")


def _list_url(category_slug: str, page: int) -> str:
    """URL de la page N d'une catégorie (page 1 = index sans suffixe). PURE."""
    if page <= 1:
        return f"{BASE}/{category_slug}/"
    return f"{BASE}/{category_slug}-p{page}/"


def parse_list_page(html: str, category_slug: str) -> List[Dict[str, str]]:
    """Cartes d'une page de catégorie -> [{fiche_id, title, website, fiche_url}].
    Écarte les entrées d'une AUTRE catégorie injectées par le moteur Arfooo
    (garde déterministe par préfixe d'URL — cf. docstring module). PURE."""
    soup = BeautifulSoup(html or "", "html.parser")
    out: List[Dict[str, str]] = []
    seen: set = set()
    prefix = f"/{category_slug}/"
    for block in soup.select("div.column_in_description_site_category"):
        a = block.select_one("a.link_black_blue_b_u[href]")
        if a is None:
            continue
        href = a.get("href", "")
        if not href.startswith(prefix):
            continue  # fiche hors catégorie ciblée (injection cross-catégorie)
        m = _FICHE_ID_RE.search(href)
        if not m or m.group(1) in seen:
            continue
        seen.add(m.group(1))
        site_span = block.select_one("span.text_characters_orange")
        out.append({
            "fiche_id": m.group(1),
            "title": a.get_text(" ", strip=True),
            "website": site_span.get_text(" ", strip=True) if site_span else "",
            "fiche_url": href if href.startswith("http") else f"{BASE}{href}",
        })
    return out


def parse_max_page(html: str) -> int:
    """Numéro de la dernière page depuis la pagination (« ... sur 5 »).
    1 si pas de pagination (catégorie tenant sur une seule page). PURE."""
    soup = BeautifulSoup(html or "", "html.parser")
    pager = soup.select_one("div.column_in_pagination")
    if pager is None:
        return 1
    pages = []
    for a in pager.select("a[href]"):
        m = _MAX_PAGE_RE.search(a.get("href", ""))
        if m:
            pages.append(int(m.group(1)))
    return max(pages) if pages else 1


def _details_map(soup: BeautifulSoup) -> Dict[str, Tag]:
    """label.title_details (minuscules) -> tag div.infos_details associé.
    Couvre les DEUX blocs « form_details » de la fiche (bloc site : Url/Lien
    retour/... et bloc société : Adresse/Code postal/Ville/Pays/Téléphone) —
    même structure DOM, clés différentes, donc un seul passage suffit. PURE."""
    out: Dict[str, Tag] = {}
    for block in soup.select("div.form_details"):
        label = block.select_one("label.title_details")
        value = block.select_one("div.infos_details")
        if label is None or value is None:
            continue
        out[label.get_text(" ", strip=True).lower()] = value
    return out


def normalize_phone_fr(raw: Optional[str]) -> Optional[str]:
    """Téléphone FR normalisé en 5 groupes de 2 chiffres (« 01 23 45 67 89 »).
    Accepte les formats bruts (chiffres collés, +33...). None si absent ou non
    reconnaissable comme un numéro FR à 10 chiffres (VIDE > FAUX). PURE."""
    if not raw:
        return None
    digits = "".join(_PHONE_DIGITS_RE.findall(raw))
    if digits.startswith("33") and len(digits) == 11:
        digits = "0" + digits[2:]
    if len(digits) != 10 or not digits.startswith("0"):
        return None
    return " ".join(digits[i:i + 2] for i in range(0, 10, 2))


def parse_fiche(html: str, fiche_id: str) -> Optional[Dict[str, Any]]:
    """Fiche profil -> dict, ou None si <h1> absent (fiche illisible) ou pays
    renseigné et hors France (garde hors-cible : répertoire généraliste,
    référence aussi des sites étrangers — VIDE > FAUX : champ absent = gardé).
    PURE. Extrait titre/adresse/CP/ville/téléphone/site."""
    soup = BeautifulSoup(html or "", "html.parser")
    h1 = soup.select_one("h1")
    if h1 is None:
        return None
    title = h1.get_text(" ", strip=True)

    details = _details_map(soup)

    def _text(key: str) -> str:
        tag = details.get(key)
        return tag.get_text(" ", strip=True) if tag is not None else ""

    pays = _text("pays").strip().lower()
    if pays and pays not in ("france", "fr"):
        return None  # hors-cible : site étranger

    website = None
    lien = details.get("lien retour")
    if lien is not None:
        a = lien.select_one("a[href]")
        if a is not None:
            website = a.get("href", "").strip() or None
    if not website:
        url_txt = _text("url")
        website = f"http://{url_txt}" if url_txt else None

    return {
        "fiche_id": fiche_id,
        "title": title,
        "address": _text("adresse"),
        "cp": _text("code postal"),
        "city": _text("ville"),
        "phone": normalize_phone_fr(_text("téléphone")),
        "website": website,
    }


class AnnuaireDecorationConnector(Connector):
    """Crawler Annuaire Décoration : pagine les 2 catégories intérieur pur
    (architecte-d-interieur, decorateur-d-interieur), puis fetch chaque fiche
    (bornée par `limit`). Entrées cross-catégorie et hors France écartées.
    HTTP injectable (tests sans réseau). `categories` injectable (dry-run
    ciblé sur une seule catégorie)."""
    name = "annuairedecoration"

    def __init__(self, http_fetch: HtmlFetch = polite_get,
                 categories: Optional[Dict[str, str]] = None) -> None:
        self.http_fetch = http_fetch
        self.categories = categories if categories is not None else CATEGORIES
        self.last_total_count = 0

    def fetch(self, since_days: int = 0, limit: int = 300,
              max_pages: int = 6, **_: Any) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for slug, label in self.categories.items():
            if len(rows) >= limit:
                break
            html = self.http_fetch(_list_url(slug, 1))
            if not html:
                continue
            n_pages = min(parse_max_page(html), max_pages or 1)
            page_rows = parse_list_page(html, slug)
            for r in page_rows:
                r["category_label"] = label
            rows.extend(page_rows)
            for page in range(2, n_pages + 1):
                if len(rows) >= limit:
                    break
                page_html = self.http_fetch(_list_url(slug, page))
                if not page_html:
                    break
                page_rows = parse_list_page(page_html, slug)
                if not page_rows:
                    break  # plus de lignes : fin de pagination
                for r in page_rows:
                    r["category_label"] = label
                rows.extend(page_rows)
        self.last_total_count = len(rows)

        out: List[Dict[str, Any]] = []
        for row in rows[:limit]:
            html = self.http_fetch(row["fiche_url"])
            if not html:
                continue
            fiche = parse_fiche(html, row["fiche_id"])
            if fiche is None:
                continue  # <h1> absent ou hors-cible (pays étranger)
            fiche["fiche_url"] = row["fiche_url"]
            fiche["category_label"] = row["category_label"]
            if not fiche.get("website") and row.get("website"):
                # Repli sur le domaine affiché en liste (site étranger déjà
                # écarté au-dessus ; ce repli ne concerne que l'absence de
                # champ "Url"/"Lien retour" sur la fiche elle-même).
                fiche["website"] = f"http://{row['website']}"
            out.append(fiche)
        return out

    def to_candidates(self, records: List[Dict[str, Any]]) -> List[LeadCandidate]:
        from datetime import date
        today = date.today()
        out: List[LeadCandidate] = []
        for f in records:
            title = (f.get("title") or "").strip()
            category_label = f.get("category_label") or "architecte d'intérieur"
            cp = (f.get("cp") or "").strip()
            city = (f.get("city") or "").strip()
            address_line = (f.get("address") or "").strip()
            address = ", ".join(
                p for p in [address_line, " ".join(filter(None, [cp, city]))] if p
            )
            proof = (
                f"Professionnel référencé dans l'annuaire {category_label} "
                "(annuairedecoration.fr)."
            )
            out.append(LeadCandidate(
                source="annuaire",
                source_ref=f"annuairedecoration:{f['fiche_id']}",
                establishment_name=title,
                city=city,
                address=address,
                main_signal="prescripteur actif",
                secondary_signals=["annuaire annuairedecoration"],
                lifecycle_label="studio_actif",
                population="architecte",
                establishment_type=category_label,
                decision_maker=None,
                detection_date=today,
                classification_text=" ".join(filter(None, [title, category_label])),
                email=None,  # aucun mailto observé sur ce répertoire (sonde)
                website=f.get("website"),
                proof_text=proof,
                proof_url=f.get("fiche_url") or "",
                # Téléphone normalisé reporté dans raw['phone'] -- seul chemin lu
                # par pipeline._process_candidate pour remplir Opportunity.phone
                # (même contrat que CFAI/UFDI/Places).
                raw={"phone": f.get("phone") or None},
            ))
        return out
