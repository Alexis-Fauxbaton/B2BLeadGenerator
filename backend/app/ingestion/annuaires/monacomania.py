"""Connecteur MonacoMania — Architectes de Monaco (monacomania.com) — A2, annuaires.

Page touristique généraliste (monacomania.com), section BUSINESS -> Architectes.
HTML statique pur (LiteSpeed/PHP, aucun anti-bot, aucun JS de rendu, confirmé
sonde). PAS de robots.txt exploitable (le domaine sert la page d'accueil en
repli sur `/robots.txt`, donc aucune interdiction explicite) : accès permissif
par défaut. Une SEULE page, NON PAGINÉE (`architectes-de-monaco.php`) : ~6
cabinets + l'Ordre des Architectes de Monaco (institution, pas un cabinet) +
un bloc publicitaire « Ajouter votre site » -- tout est visible en un seul
fetch, aucun crawl de fiches détaillées nécessaire (contrairement à
CFAI/UFDI/Mon Architecte d'Intérieur/Annuaire Décoration).

Chaque carte cabinet est une `<table>` à 2 colonnes : photo+lien (col. 1),
bloc info (col. 2) où le nom est dans `span.style133 strong` et le reste
(description libre, adresse, « MC-98000 MONACO », « Tel: +377 ... », lien
« + info » ») dans un texte à plat, en lignes -- la structure exacte des
`<span class="style5">` imbriqués VARIE d'une fiche à l'autre (observé sur
les 6 cabinets réels : parfois un seul span, parfois deux, parfois du texte
hors span), donc l'extraction se fait en LIGNES (une par nœud texte, via
`get_text("\\n", strip=True)`) puis en dépilant depuis la FIN (lien « + info »,
puis « Tel: », puis « MC-98000 MONACO », puis la ligne d'adresse restante) --
robuste à cette variation, déterministe, PURE.

Garde hors-cible : l'Ordre des Architectes de Monaco (institution
professionnelle, pas un cabinet commercial) partage la même structure de
carte (`span.style133 strong`) mais n'a NI adresse NI téléphone (juste un nom
et un lien « + info »). Écarté déterministement (ni tél ni adresse -> None) :
contrairement au reste de la population A2 (VIDE > FAUX quand UN SEUL champ
manque), l'absence simultanée des DEUX est le signal fiable d'une fiche
non-cabinet sur cette page précise (aucun vrai cabinet sondé n'a les deux
vides). Le bloc publicitaire « Ajouter votre site » est écarté
structurellement (nom dans `span.style9` + `<a>`, pas `span.style133 strong`
-- jamais sélectionné).

Téléphone international monégasque (+377, 8 chiffres) normalisé en
`normalize_phone_mc`. Pas d'email en clair (sonde). Fail-soft partout, HTTP
injectable (tests sans réseau, snapshot réel dans
tests/fixtures/monacomania/, récupéré poliment le 2026-07-17, throttle >=
2,5 s).

Garde hors-cible « intérieur » [défaut qualité #3, revue Alexis 2026-07-18] :
monacomania.com est un annuaire d'ARCHITECTES GÉNÉRALISTES monégasques -- la
majorité des ~6 cabinets référencés sont du bâtiment PUR (urbanisme,
construction), hors cible LumaPro (aménagement/décoration d'intérieur). Même
exigence que les autres connecteurs annuaire (CFAI/UFDI/annuaire_decoration/
mon_architecte_interieur) : `parse_card` ne garde une carte QUE si son
descriptif libre évoque explicitement l'intérieur/la décoration/
l'aménagement (`_INTERIOR_MENTION_RE`) -- sinon écartée (VIDE > FAUX : sur les
6 cabinets réels sondés, seul « ARCH - FRED GENIN » qualifie ; on préfère 1
fiche juste que 6 douteuses plutôt que de deviner sur un simple nom
d'enseigne). Aucun nouveau fetch réseau : le filtre reste purement déterministe
sur le texte déjà présent dans la page annuaire (pas de crawl du site propre
de chaque cabinet)."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup

from ..base import Connector, LeadCandidate
from .http import HtmlFetch, polite_get

BASE = "https://www.monacomania.com"
LIST_URL = f"{BASE}/architectes-de-monaco.php"

_POSTAL_CITY_RE = re.compile(r"^MC-(\d{5})\s+MONACO$", re.IGNORECASE)
_TEL_LINE_RE = re.compile(r"^tel\s*:?", re.IGNORECASE)
_PHONE_DIGITS_RE = re.compile(r"\d")
_SLUG_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
# Garde hors-cible + tiering : mention intérieur/décoration/aménagement dans le
# descriptif libre de la carte (cf. docstring module, défaut qualité #3).
# Utilisée à la fois par `parse_card` (garde -- rejette si absente) et
# `to_candidates` (tiering -- inchangé, mais désormais toujours vrai pour une
# carte gardée : `parse_card` garantit déjà la présence de la mention).
_INTERIOR_MENTION_RE = re.compile(r"int[ée]rieur|d[ée]corat|am[ée]nagement", re.IGNORECASE)


def normalize_phone_mc(raw: Optional[str]) -> Optional[str]:
    """Téléphone monégasque normalisé en « +377 XX XX XX XX » (indicatif +377,
    8 chiffres). None si absent ou non reconnaissable comme un numéro
    monégasque à 8 chiffres (VIDE > FAUX). PURE."""
    if not raw:
        return None
    digits = "".join(_PHONE_DIGITS_RE.findall(raw))
    if not digits.startswith("377") or len(digits) != 11:
        return None
    rest = digits[3:]
    return "+377 " + " ".join(rest[i:i + 2] for i in range(0, 8, 2))


def slugify(name: str) -> str:
    """Nom de cabinet -> identifiant stable (minuscules, séparateurs `-`),
    utilisé en `source_ref` (page sans identifiant numérique). PURE."""
    s = _SLUG_NON_ALNUM_RE.sub("-", (name or "").strip().lower()).strip("-")
    return s or "sans-nom"


def parse_card(html: str) -> Optional[Dict[str, Any]]:
    """Une carte (table à 2 colonnes) -> dict, ou None si <span class="style133">
    absent (pas une carte cabinet), fiche institutionnelle (ni tél ni adresse),
    ou descriptif sans mention intérieur/décoration/aménagement (gardes
    hors-cible, cf. docstring module). PURE."""
    soup = BeautifulSoup(html or "", "html.parser")
    name_span = soup.select_one("span.style133")
    if name_span is None:
        return None
    tds = soup.select("td")
    if len(tds) < 2:
        return None
    photo_td, info_td = tds[0], tds[-1]

    strong = name_span.find("strong")
    name = (strong.get_text(" ", strip=True) if strong else name_span.get_text(" ", strip=True)).strip()
    if not name:
        return None

    lines = [l for l in info_td.get_text("\n", strip=True).split("\n") if l]
    # lines[0] == le nom (répété dans le bloc info) -- déjà extrait ci-dessus.
    tail = lines[1:]
    if tail and tail[-1].strip() == "+ info »":
        tail = tail[:-1]

    phone_raw = None
    if tail and _TEL_LINE_RE.match(tail[-1]):
        phone_raw = tail.pop()

    has_postal = bool(tail and _POSTAL_CITY_RE.match(tail[-1]))
    if has_postal:
        tail.pop()

    address = None
    if has_postal and tail:
        address = tail.pop()

    description = " ".join(tail).strip()
    phone = normalize_phone_mc(phone_raw)

    if not phone and not address:
        return None  # fiche institutionnelle (ex. Ordre des Architectes) : pas un cabinet

    if not _INTERIOR_MENTION_RE.search(description):
        return None  # hors-cible : cabinet bâtiment pur (garde intérieur, défaut #3)

    website = None
    link = info_td.select_one("a[href]")
    if link is not None:
        website = link.get("href", "").strip() or None
    if not website:
        photo_link = photo_td.select_one("a[href]")
        if photo_link is not None:
            website = photo_link.get("href", "").strip() or None

    return {
        "slug": slugify(name),
        "name": name,
        "description": description,
        "address": address or "",
        "city": "Monaco" if has_postal else "",
        "phone": phone,
        "website": website,
    }


def parse_list(html: str) -> List[Dict[str, Any]]:
    """Page complète -> liste de cartes cabinet (institution et bloc
    publicitaire écartés). Dédup par slug (une carte par cabinet). PURE."""
    soup = BeautifulSoup(html or "", "html.parser")
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for name_span in soup.select("span.style133"):
        table = name_span.find_parent("table")
        if table is None:
            continue
        card = parse_card(str(table))
        if card is None:
            continue
        if card["slug"] in seen:
            continue
        seen.add(card["slug"])
        out.append(card)
    return out


class MonacomaniaConnector(Connector):
    """Crawler MonacoMania : UNE seule page (pas de pagination, pas de fiches
    détaillées à fetcher séparément -- tout est déjà sur la page liste).
    HTTP injectable (tests sans réseau)."""
    name = "monacomania"

    def __init__(self, http_fetch: HtmlFetch = polite_get) -> None:
        self.http_fetch = http_fetch
        self.last_total_count = 0

    def fetch(self, since_days: int = 0, limit: int = 50, **_: Any) -> List[Dict[str, Any]]:
        html = self.http_fetch(LIST_URL)
        if not html:
            self.last_total_count = 0
            return []
        rows = parse_list(html)
        self.last_total_count = len(rows)
        return rows[:limit]

    def to_candidates(self, records: List[Dict[str, Any]]) -> List[LeadCandidate]:
        from datetime import date
        today = date.today()
        out: List[LeadCandidate] = []
        for r in records:
            name = (r.get("name") or "").strip()
            description = (r.get("description") or "").strip()
            secondary = ["annuaire monacomania"]
            establishment_type = "architecte"
            if _INTERIOR_MENTION_RE.search(description):
                # Mention explicite intérieur/décoration/aménagement dans le
                # descriptif libre (ex. ARCH - FRED GENIN) -- signal utile pour
                # le tiering aval. Depuis la garde hors-cible de `parse_card`
                # (défaut #3), ce test est TOUJOURS vrai pour une carte issue
                # de `fetch()` (la garde a déjà écarté les cartes sans mention)
                # ; il reste exercé directement par `to_candidates` sur des
                # records construits à la main (tests unitaires).
                secondary.append("mention architecture d'intérieur")
                establishment_type = "architecte d'intérieur"
            proof = "Cabinet d'architecture référencé dans l'annuaire monacomania.com (Monaco)."
            out.append(LeadCandidate(
                source="annuaire",
                source_ref=f"monacomania:{r['slug']}",
                establishment_name=name,
                city=r.get("city") or "",
                address=r.get("address") or "",
                main_signal="prescripteur actif",
                secondary_signals=secondary,
                lifecycle_label="studio_actif",
                population="architecte",
                establishment_type=establishment_type,
                decision_maker=None,
                detection_date=today,
                classification_text=" ".join(filter(None, [name, description])),
                email=None,  # pas d'email en clair sur cette page (sonde)
                website=r.get("website"),
                proof_text=proof,
                proof_url=LIST_URL,
                # Téléphone normalisé reporté dans raw['phone'] -- seul chemin lu
                # par pipeline._process_candidate pour remplir Opportunity.phone
                # (même contrat que CFAI/UFDI/Annuaire Décoration/Mon Architecte
                # d'Intérieur/Places).
                raw={"phone": r.get("phone") or None},
            ))
        return out
