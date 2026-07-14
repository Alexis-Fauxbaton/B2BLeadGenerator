"""Moteur de découverte de SITE (Brique A du « chantier fiches gratuit »).

Répond à UNE question : « quel est le site PROPRE de CETTE fiche ? ». Une fois
le site trouvé et écrit dans ``website`` (par ``find_sites.py --apply``), les
passes existantes ``enrich_phones`` / ``enrich_site_contacts`` prennent le
relais, INCHANGÉES. Ce module ne scrape PAS de contacts.

Doctrine **VIDE > FAUX** : mieux vaut aucun site attribué qu'un site d'un
autre commerce. Le VERROU D'IDENTITÉ est strict et NE S'AFFAIBLIT JAMAIS :

  - **A** (le nom matche le site) : A1 contenu (tous les tokens significatifs
    du nom dans title/h1/og:site_name) OU A2 domaine (distance tolérante /
    sous-chaîne contiguë entre le cœur de domaine et les tokens) ;
  - **B** (>= 1 corroboration INDÉPENDANTE, géo/dirigeant/immatriculation) :
    ville, code postal, nom de famille d'un dirigeant, SIREN, SIRET.

Site attribué si la voie **A ET B** passe. Calibrage 2026-07-14 : une VOIE **C**
alternative rattrape les raisons sociales qui ne matchent NI le contenu NI le
domaine (nom abrégé/fusionné, ex. « CAT » pour Catherine) — nom COMPLET du
dirigeant (prénom + nom) présent sur le site + signal FORT géo/immatriculation
(cp/siren/siret). Le premier candidat qui passe (A+B OU C) gagne, jamais deux
sites attribués à une même fiche.

Couche de RECHERCHE (fiabilisée 2026-07-14) : liste ORDONNÉE de moteurs publics
(:data:`_ENGINES` — DuckDuckGo HTML puis, en repli, Bing HTML), chacun avec son
constructeur d'URL, son parseur pur et sa détection d'échec. Un moteur qui « ne
sert pas » (HTTP 202 / défi anti-bot / corps vide / malformé -> ``fetch`` rend
None ou un parse vide) déclenche le repli sur le suivant ; si TOUS les moteurs
sont muets, la recherche est déclarée NON SERVIE (verdict fiche
``search_unavailable``, RÉESSAYABLE — distinct de ``no_candidate`` où un moteur
a réellement répondu mais sans candidat propre). Aucun contournement anti-bot :
que de la politesse (cadence lente, retry avec backoff, repli sur un autre
moteur public).

Cadence : DEUX gates distincts. Les fetchs de PAGES candidates gardent
``_MIN_INTERVAL`` >= 2,5 s (``_throttle``). Les RECHERCHES moteur ont leur
propre gate ``_SEARCH_MIN_INTERVAL`` >= 10 s + petit jitter (``_search_throttle``),
et sur un défi anti-bot (202/corps « anomaly ») font UN SEUL retry après un
backoff LONG (30-60 s), sinon abandon fail-soft (None). User-Agent =
``website_scraper.HEADERS`` (imposé par le brief). ``_polite_get`` est le SEUL
point qui touche réellement le réseau : il ROUTE les URL de moteur vers la
cadence recherche (``_polite_search_get``) et les pages vers la cadence 2,5 s.
La fonction publique :func:`find_site` prend un paramètre ``fetch`` injectable
(patron ``annuaires.http.HtmlFetch``) — les tests passent un faux ``fetch``
alimenté par des fixtures HTML, zéro appel réseau.

Cache : réutilise ``verdict_cache`` (table ``handle_verdicts``) SANS aucune
migration, avec des clés préfixées ``sitefind:`` (jamais de collision avec les
verdicts CHR/``arch:``) :
  - ``sitefind:q:<sha1(requête normalisée)>`` : liste JSON des URLs résultats
    DDG (verdict="search"), pour ne JAMAIS refaire deux fois la même requête
    réseau (partagé entre fiches) ;
  - ``sitefind:opp:<id>`` (repli ``sitefind:siren:<siren>``) : verdict de LA
    FICHE (found/locked_out/no_candidate), ``confidence`` = URL trouvée ou
    None — un run repris saute les fiches déjà tranchées via
    ``verdict_cache.should_rejudge`` (fenêtre de +2 mois par défaut, ces
    verdicts n'étant dans aucune fenêtre ``REVISIT_MONTHS`` dédiée).

Ni ``sitefind:q:`` ni ``sitefind:opp:`` ne sont écrits sur une RECHERCHE NON
SERVIE (aucun moteur n'a répondu : ``fetch`` -> None / défi / vide / malformé) :
le cache de requête ne stocke QUE des listes de résultats réellement servies
(``SearchOutcome.served``), et le verdict fiche ``search_unavailable`` n'est
JAMAIS mis en cache (comme ``error``). Sinon une panne anti-bot ponctuelle (les
202 de DDG) verrouillerait la fiche en ``no_candidate`` pendant la fenêtre de
revisite (+2 mois), alors qu'on n'a jamais pu interroger un moteur. Garde
supplémentaire : un ``no_candidate`` obtenu alors qu'AU MOINS une requête de la
séquence a été muette n'est pas non plus mis en cache (``any_muted``).
"""
from __future__ import annotations

import base64
import difflib
import hashlib
import json
import random
import re
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from html import unescape as _html_unescape
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import requests
from sqlmodel import Session

from .. import verdict_cache
from ..annuaires.http import HtmlFetch
from .own_site import is_directory as _is_directory
from .own_site import own_site
from .website_scraper import HEADERS, _PAGE_CAP, contact_page_urls, home_url_variants

# --------------------------------------------------------------------------- #
# Réseau : requête POLIE, seul point qui touche réellement le réseau.         #
# --------------------------------------------------------------------------- #

# Cadence PAGES candidates : >= 2,5 s (même patron ``annuaires/http.py``).
_MIN_INTERVAL = 2.5
_last_call = [0.0]

# Cadence RECHERCHES moteur : gate SÉPARÉ, plus lent (>= 10 s) + jitter, et un
# UNIQUE retry après un backoff LONG sur défi anti-bot. Jamais mêlé au throttle
# des pages : une recherche est bien plus « chère » côté anti-bot qu'un fetch de
# page propre.
_SEARCH_MIN_INTERVAL = 10.0
_SEARCH_JITTER = 2.0            # jitter aléatoire ajouté dans [0, 2] s
_SEARCH_BACKOFF = (30.0, 60.0)  # backoff LONG avant l'unique retry sur défi
_last_search_call = [0.0]

_DDG_URL = "https://html.duckduckgo.com/html/?q={}"
_BING_URL = "https://www.bing.com/search?q={}"

# Hôtes des moteurs de recherche : ``_polite_get`` route ces URL vers la cadence
# recherche dédiée (>= 10 s + retry), le reste (pages) vers la cadence 2,5 s.
_SEARCH_HOSTS = ("html.duckduckgo.com", "duckduckgo.com", "www.bing.com", "bing.com")

# Marqueurs d'une page de DÉFI anti-bot (HTTP 202, ou corps 200 « anomaly » /
# captcha) : la recherche N'EST PAS servie -> retry après backoff puis repli.
# Aucun contournement : on RENONCE proprement, on ne déjoue jamais le défi.
_CHALLENGE_MARKERS = (
    "anomaly", "unusual traffic", "are you a robot", "verify you are a human",
    "detected unusual", "please solve", "captcha", "/challenge",
)


def _throttle() -> None:
    wait = _MIN_INTERVAL - (time.monotonic() - _last_call[0])
    if wait > 0:
        time.sleep(wait)
    _last_call[0] = time.monotonic()


def _search_throttle() -> None:
    """Gate de cadence DÉDIÉ aux recherches moteur (>= 10 s + jitter), distinct
    du throttle 2,5 s des pages."""
    wait = _SEARCH_MIN_INTERVAL - (time.monotonic() - _last_search_call[0])
    wait += random.uniform(0.0, _SEARCH_JITTER)
    if wait > 0:
        time.sleep(wait)
    _last_search_call[0] = time.monotonic()


def _looks_like_challenge(html: Optional[str]) -> bool:
    """True si le corps ressemble à une page de défi anti-bot (marqueurs
    :data:`_CHALLENGE_MARKERS`). Pure, testable sans réseau."""
    if not html:
        return False
    low = html.lower()
    return any(marker in low for marker in _CHALLENGE_MARKERS)


def _search_attempt(url: str) -> Optional[str]:
    """UNE tentative de GET moteur : renvoie le HTML sur 200-HTML NON-défi,
    sinon None (statut != 200 dont 202, MIME non-HTML, corps de défi, réseau).
    Ne throttle PAS (la cadence est gérée par :func:`_polite_search_get`)."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
    except Exception:
        return None
    if resp.status_code != 200:
        return None
    if "text/html" not in resp.headers.get("content-type", ""):
        return None
    if _looks_like_challenge(resp.text):
        return None
    return resp.text


def _polite_search_get(url: str) -> Optional[str]:
    """GET de RECHERCHE poli : cadence dédiée (>= 10 s + jitter) puis, sur défi
    anti-bot / échec (202, MIME non-HTML, corps « anomaly »), UN SEUL retry après
    un backoff LONG (30-60 s). Si le défi persiste -> None (abandon fail-soft,
    JAMAIS de contournement). User-Agent ``website_scraper.HEADERS``."""
    _search_throttle()
    text = _search_attempt(url)
    if text is None:
        time.sleep(random.uniform(*_SEARCH_BACKOFF))
        _search_throttle()
        text = _search_attempt(url)
    return text[:_PAGE_CAP] if text is not None else None


def _is_search_url(url: str) -> bool:
    """True si l'URL vise un moteur de recherche (cadence dédiée)."""
    return urlparse(url).netloc.lower() in _SEARCH_HOSTS


def _polite_get(url: str) -> Optional[str]:
    """Point réseau UNIQUE du module. Route les URL de MOTEUR (DDG/Bing) vers la
    cadence recherche (``_polite_search_get`` : >= 10 s + retry sur défi), et les
    PAGES candidates vers la cadence 2,5 s (``_throttle``). User-Agent
    ``website_scraper.HEADERS`` (imposé par le brief), fail-soft (réseau / statut
    != 200 / MIME non-HTML -> None)."""
    if _is_search_url(url):
        return _polite_search_get(url)
    _throttle()
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200 or "text/html" not in resp.headers.get("content-type", ""):
            return None
        return resp.text[:_PAGE_CAP]
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Parsing des résultats DDG (pur, testable sans réseau).                      #
# --------------------------------------------------------------------------- #

_RESULT_A_RE = re.compile(r'<a\b[^>]*class="result__a"[^>]*>', re.I)
_HREF_ATTR_RE = re.compile(r'href="([^"]*)"', re.I)
_MAX_DDG_RESULTS = 8


def _decode_ddg_href(href: str) -> Optional[str]:
    """Décode une ancre de résultat DDG : redirection ``//duckduckgo.com/l/
    ?uddg=<url-encodée>&rut=...`` -> URL cible décodée ; URL déjà directe ->
    gardée telle quelle."""
    href = _html_unescape(href.strip())
    if not href:
        return None
    if href.startswith("//"):
        href = "https:" + href
    if "duckduckgo.com/l/" in href:
        parsed = urlparse(href)
        vals = parse_qs(parsed.query).get("uddg")
        return unquote(vals[0]) if vals else None
    if href.startswith("http"):
        return href
    return None


def parse_ddg_results(html: Optional[str]) -> List[str]:
    """Ancres de résultats (``class="result__a"``) -> URLs cibles décodées,
    dédupliquées par DOMAINE enregistrable (ordre d'apparition conservé),
    bornées à :data:`_MAX_DDG_RESULTS`. Fonction pure, testable sans réseau."""
    if not html:
        return []
    out: List[str] = []
    seen: set = set()
    for tag in _RESULT_A_RE.findall(html):
        m = _HREF_ATTR_RE.search(tag)
        if not m:
            continue
        url = _decode_ddg_href(m.group(1))
        if not url:
            continue
        domain = _domain(url)
        if not domain or domain in seen:
            continue
        seen.add(domain)
        out.append(url)
        if len(out) >= _MAX_DDG_RESULTS:
            break
    return out


def _domain(url: Optional[str]) -> Optional[str]:
    """Domaine enregistrable (sans ``www.``) d'une URL, None si illisible.
    Patron ``enrich_phones._site_domain``."""
    if not url:
        return None
    host = urlparse(url if url.startswith("http") else "http://" + url).netloc.lower()
    return host[4:] if host.startswith("www.") else host or None


# --------------------------------------------------------------------------- #
# Parsing des résultats Bing HTML (MOTEUR DE REPLI, pur, testable sans réseau).#
# --------------------------------------------------------------------------- #

# Résultats organiques Bing : blocs ``<li class="b_algo">`` dont le titre
# ``<h2><a href="...">`` porte la cible. Bing enveloppe parfois l'href dans une
# redirection ``/ck/a?...&u=a1<base64-url-safe>`` -> on décode le paramètre ``u``.
_BING_ALGO_RE = re.compile(r'<li class="b_algo".*?</li>', re.I | re.S)
_BING_H2_HREF_RE = re.compile(r'<h2\b[^>]*>.*?<a\b[^>]*href="([^"]+)"', re.I | re.S)


def _decode_bing_href(href: str) -> Optional[str]:
    """Décode une ancre de résultat Bing : URL directe gardée telle quelle ;
    redirection ``.../ck/a?...&u=a1<base64>`` -> URL cible décodée. None si
    illisible."""
    href = _html_unescape(href.strip())
    if not href:
        return None
    if href.startswith("//"):
        href = "https:" + href
    if "bing.com/ck/a" in href or href.startswith("/ck/a"):
        vals = parse_qs(urlparse(href).query).get("u")
        if not vals:
            return None
        token = vals[0]
        if token.startswith("a1"):  # préfixe de version Bing
            token = token[2:]
        pad = "=" * (-len(token) % 4)
        try:
            decoded = base64.urlsafe_b64decode(token + pad).decode("utf-8", "ignore")
        except Exception:
            return None
        return decoded if decoded.startswith("http") else None
    return href if href.startswith("http") else None


def parse_bing_results(html: Optional[str]) -> List[str]:
    """Résultats organiques Bing (``<li class="b_algo">`` -> ``<h2><a href>``)
    -> URLs cibles décodées, dédupliquées par DOMAINE enregistrable (ordre
    conservé), bornées à :data:`_MAX_DDG_RESULTS`. Pure, testable sans réseau."""
    if not html:
        return []
    out: List[str] = []
    seen: set = set()
    for block in _BING_ALGO_RE.findall(html):
        m = _BING_H2_HREF_RE.search(block)
        if not m:
            continue
        url = _decode_bing_href(m.group(1))
        if not url:
            continue
        domain = _domain(url)
        if not domain or domain in seen:
            continue
        seen.add(domain)
        out.append(url)
        if len(out) >= _MAX_DDG_RESULTS:
            break
    return out


# Annuaires d'entreprises / agrégateurs : leurs pages portent le nom + SIREN +
# adresse EXACTS de la fiche, donc passeraient A ET B TRIVIALEMENT — mais ce
# n'est JAMAIS le site PROPRE du lead (doctrine VIDE > FAUX). La blocklist est
# désormais PARTAGÉE dans ``own_site`` (``DIRECTORY_HOSTS`` + ``DIRECTORY_URL_RE``,
# étendue au gate du 2026-07-14) et appliquée dès ``own_site()`` ; ``_is_directory``
# (importé) reste une garde explicite en défense de profondeur.


# --------------------------------------------------------------------------- #
# Normalisation du nom (pure).                                                #
# --------------------------------------------------------------------------- #

# Formes juridiques retirées en tokens ENTIERS (word-boundary), après passage
# en minuscules/accents retirés.
_LEGAL_FORMS = (
    "sarl", "sas", "sasu", "eurl", "sa", "selarl", "selas", "ste", "societe",
    "sci", "snc", "scp", "eirl", "ei", "sc",
)
_LEGAL_FORMS_RE = re.compile(r"\b(?:" + "|".join(_LEGAL_FORMS) + r")\b")
_PUNCT_RE = re.compile(r"[^a-z0-9]+")
_MULTI_SPACE_RE = re.compile(r"\s+")

# Mots génériques du métier (studio/agence/architecte...) : filtrés des tokens
# significatifs pour éviter qu'un site matche sur un mot aussi banal seul.
_GENERIC_TOKENS = frozenset({
    "studio", "agence", "atelier", "architecture", "architecte", "interieur",
    "interieurs", "design", "decoration", "deco", "and", "the", "paris",
})


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def normalize_name(raw: Optional[str]) -> str:
    """Minuscules, accents retirés, formes juridiques retirées (tokens
    entiers), ponctuation -> espaces, espaces multiples repliés. Pure."""
    if not raw:
        return ""
    s = _strip_accents(raw.lower())
    s = _LEGAL_FORMS_RE.sub(" ", s)
    s = _PUNCT_RE.sub(" ", s)
    return _MULTI_SPACE_RE.sub(" ", s).strip()


def significant_tokens(name: str) -> List[str]:
    """Tokens normalisés de longueur >= 3, moins la stoplist générique
    (:data:`_GENERIC_TOKENS`). Si le nom entier EST générique (tous les tokens
    filtrés), repli sur les tokens >= 3 BRUTS — ne jamais rendre une liste vide
    quand des tokens existent (sinon le verrou A serait trivialement vrai)."""
    tokens = [t for t in normalize_name(name).split() if len(t) >= 3]
    filtered = [t for t in tokens if t not in _GENERIC_TOKENS]
    return filtered if filtered else tokens


# --------------------------------------------------------------------------- #
# Extraction des marqueurs d'identité d'une page (pure).                      #
# --------------------------------------------------------------------------- #

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.I | re.S)
_OG_SITE_NAME_RE = re.compile(
    r'<meta[^>]+property=["\']og:site_name["\'][^>]+content=["\']([^"\']*)["\']', re.I)
_OG_SITE_NAME_RE2 = re.compile(
    r'<meta[^>]+content=["\']([^"\']*)["\'][^>]+property=["\']og:site_name["\']', re.I)
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_tags(s: str) -> str:
    return _TAG_RE.sub(" ", s)


def extract_identity_markers(html: str) -> str:
    """Texte agrégé de ``<title>`` + ``<h1>`` + ``og:site_name`` d'une page
    (tags internes retirés, entités HTML décodées). Fonction pure."""
    if not html:
        return ""
    parts: List[str] = []
    parts.extend(_strip_tags(m) for m in _TITLE_RE.findall(html))
    parts.extend(_strip_tags(m) for m in _H1_RE.findall(html))
    parts.extend(_OG_SITE_NAME_RE.findall(html))
    parts.extend(_OG_SITE_NAME_RE2.findall(html))
    return _html_unescape(" ".join(parts))


def _extract_title(html: str) -> str:
    m = _TITLE_RE.search(html)
    return _strip_tags(m.group(1)).strip() if m else ""


# --------------------------------------------------------------------------- #
# Verrou A — le nom matche le site (A1 contenu OU A2 domaine).                #
# --------------------------------------------------------------------------- #

def _domain_core(domain: str) -> str:
    """Cœur de domaine (sans TLD ni caractères non alphanumériques)."""
    core = domain.rsplit(".", 1)[0] if domain and "." in domain else (domain or "")
    return re.sub(r"[^a-z0-9]", "", core.lower())


def _domain_segments(domain: str) -> List[str]:
    """Segments alphanumériques du domaine SANS son TLD (découpage sur tirets/
    points/underscores). Ex. ``atelier-dupont.fr`` -> ``["atelier", "dupont"]`` ;
    ``archives.territoiredebelfort.fr`` -> ``["archives", "territoiredebelfort"]``."""
    label = domain.rsplit(".", 1)[0] if domain and "." in domain else (domain or "")
    return [seg for seg in re.split(r"[^a-z0-9]+", label.lower()) if seg]


def _domain_matches_name(domain: Optional[str], name_tokens: List[str]) -> bool:
    """A2 : le domaine ~ le nom, par TOKENS COMPLETS (durcissement 2026-07-14 —
    plus de sous-chaîne laxiste : ``archivest`` NE matche PLUS ``archives...``).

    Deux voies, toutes deux exigeant une correspondance de token ENTIER :
      - distance tolérante (``difflib`` >= 0.85) entre le cœur de domaine et la
        concaténation des tokens significatifs (ex. ``atelierdupont.fr`` ~
        « atelier dupont ») ;
      - chaque token significatif est un SEGMENT complet du domaine (séparé par
        tiret/point/underscore) — ex. ``atelier-dupont.fr`` ~ « dupont »."""
    if not domain or not name_tokens:
        return False
    core = _domain_core(domain)
    concat = "".join(name_tokens)
    if not core or not concat:
        return False
    if difflib.SequenceMatcher(None, core, concat).ratio() >= 0.85:
        return True
    segments = set(_domain_segments(domain))
    return all(tok in segments for tok in name_tokens)


def _check_lock_a(name_tokens: List[str], markers_text: str, domain: str) -> Optional[str]:
    """Verrou A : "A1_content" (tous les tokens dans title/h1/og), sinon
    "A2_domain" (cœur de domaine ~ tokens), sinon None."""
    if name_tokens:
        marker_words = set(normalize_name(markers_text).split())
        if all(tok in marker_words for tok in name_tokens):
            return "A1_content"
    if _domain_matches_name(domain, name_tokens):
        return "A2_domain"
    return None


# --------------------------------------------------------------------------- #
# Verrou B — corroboration indépendante (ville/cp/dirigeant/siren/siret).     #
# --------------------------------------------------------------------------- #

_CP_RE = re.compile(r"\b\d{5}\b")


def _extract_postal_code(address: Optional[str]) -> Optional[str]:
    if not address:
        return None
    m = _CP_RE.search(address)
    return m.group(0) if m else None


def _only_digits(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    digits = re.sub(r"\D", "", value)
    return digits or None


def _dirigeant_full_name(dirigeants: Optional[List[str]]) -> Optional[str]:
    """Nom complet (prénom + nom) du 1er dirigeant déclaré, sans le rôle en
    suffixe (ex. « Samuel Afif, Président » -> « Samuel Afif »)."""
    if not dirigeants:
        return None
    first = dirigeants[0].split(",")[0].strip()
    return first or None


def _dirigeant_family_name(dirigeants: Optional[List[str]]) -> Optional[str]:
    """Nom de famille normalisé (dernier mot du nom complet) du 1er dirigeant.
    None si absent ou trop court (< 3 caractères, garde anti-faux-positif)."""
    full = _dirigeant_full_name(dirigeants)
    if not full:
        return None
    parts = full.split()
    if not parts:
        return None
    family = normalize_name(parts[-1])
    return family if len(family) >= 3 else None


def _check_lock_b(opp: Any, aggregated_text: str) -> List[str]:
    """Corroboration indépendante du nom : ville, code postal, nom de famille
    d'un dirigeant (texte agrégé normalisé) ; SIREN/SIRET (chiffres bruts,
    séparateurs supprimés, gardes de frontière). Sous-ensemble ordonné de
    ``["ville", "cp", "dirigeant", "siren", "siret"]``."""
    signals: List[str] = []
    normalized = normalize_name(aggregated_text)
    words = set(normalized.split())

    city_norm = normalize_name(getattr(opp, "city", None) or "")
    if city_norm and city_norm in normalized:
        signals.append("ville")

    cp = _extract_postal_code(getattr(opp, "address", None))
    if cp and re.search(r"(?<!\d)" + re.escape(cp) + r"(?!\d)", aggregated_text):
        signals.append("cp")

    family = _dirigeant_family_name(getattr(opp, "dirigeants", None))
    if family and family in words:
        signals.append("dirigeant")

    compact_digits = re.sub(r"[ .\-]", "", aggregated_text)
    siren = _only_digits(getattr(opp, "siren", None))
    if siren and re.search(r"(?<!\d)" + re.escape(siren) + r"(?!\d)", compact_digits):
        signals.append("siren")

    siret = _only_digits(getattr(opp, "siret", None))
    if siret and re.search(r"(?<!\d)" + re.escape(siret) + r"(?!\d)", compact_digits):
        signals.append("siret")

    return signals


# Signaux de corroboration FORTS (discriminants) : code postal, dirigeant,
# SIREN, SIRET. « ville » est un signal FAIBLE (une simple coïncidence
# géographique suffit à le déclencher — cf. gate 2026-07-14 : DAMSO INTERIEURS
# à Lyon a matché la billetterie d'un concert de Damso à Lyon).
_STRONG_B_SIGNALS = frozenset({"cp", "dirigeant", "siren", "siret"})

# Signaux FORTS géo/immatriculation exigés par la VOIE C (cp / siren / siret) :
# jamais « ville » (coïncidence géographique) ni « dirigeant » (patronyme seul,
# déjà porté par le nom complet exigé côté C).
_STRONG_GEO_IMMAT = frozenset({"cp", "siren", "siret"})


def _corroboration_ok(signals: List[str]) -> bool:
    """La corroboration B est SUFFISANTE si elle contient au moins un signal
    FORT (cp/dirigeant/siren/siret) OU au moins deux signaux distincts
    (« ville » + un second). « ville » SEULE ne suffit PLUS (durcissement
    2026-07-14 : un lieu peut coïncider par pur hasard)."""
    if any(sig in _STRONG_B_SIGNALS for sig in signals):
        return True
    return len(set(signals)) >= 2


# --------------------------------------------------------------------------- #
# VOIE C — identité par le NOM COMPLET DU DIRIGEANT (calibrage 2026-07-14).    #
# Alternative au verrou A pour les raisons sociales qui ne matchent pas la     #
# marque du site (nom abrégé/fusionné : « CAT » pour Catherine…).             #
# --------------------------------------------------------------------------- #

def _dirigeant_identity_tokens(dirigeants: Optional[List[str]]) -> List[str]:
    """Tokens normalisés (>= 3 caractères) du nom COMPLET du 1er dirigeant
    (prénom + nom). Renvoie ``[]`` si le nom complet ne fournit pas AU MOINS
    DEUX tokens significatifs : la voie C exige le nom entier (prénom ET nom),
    jamais le seul patronyme — celui-ci est déjà couvert par le signal B
    « dirigeant » et serait trop peu discriminant seul."""
    full = _dirigeant_full_name(dirigeants)
    if not full:
        return []
    tokens = [t for t in normalize_name(full).split() if len(t) >= 3]
    return tokens if len(tokens) >= 2 else []


def _check_lock_c(opp: Any, aggregated_text: str, b_signals: List[str]) -> bool:
    """VOIE C d'identité (calibrage 2026-07-14) — pour les raisons sociales qui
    ne matchent NI le contenu NI le domaine du site (nom abrégé/fusionné, ex.
    « CAT LASSALLE » alors que le site parle de « Catherine Lassalle »).

    Identité VALIDE, même sans verrou A, SI ET SEULEMENT SI :
      - le nom COMPLET du dirigeant (prénom ET nom, cf.
        :func:`_dirigeant_identity_tokens`) est présent dans le texte agrégé
        (home racine + mentions/contact), ET
      - un signal FORT géo/immatriculation (cp / siren / siret) y figure aussi.

    Deux ancres indépendantes et hautement discriminantes (un nom + prénom
    complets coïncidant AVEC un CP/SIREN par pur hasard est improbable) → sûr au
    regard de VIDE > FAUX. « ville » SEULE ne déclenche JAMAIS la voie C (même
    doctrine que le verrou B). Ne remplace PAS la blocklist d'agrégateurs : un
    annuaire est écarté en amont (``own_site``/``is_directory``) et sa home
    racine générique ne cite de toute façon aucun dirigeant nommé."""
    tokens = _dirigeant_identity_tokens(getattr(opp, "dirigeants", None))
    if not tokens:
        return False
    words = set(normalize_name(aggregated_text).split())
    if not all(tok in words for tok in tokens):
        return False
    return any(sig in _STRONG_GEO_IMMAT for sig in b_signals)


# --------------------------------------------------------------------------- #
# Fetch d'un candidat (home + pages contact/mentions), fetch injecté.         #
# --------------------------------------------------------------------------- #

# Réimplémentation de ``website_scraper._fetch_home``/``_fetch_html`` avec
# ``fetch`` INJECTABLE : les originales appellent ``requests.get`` en dur (pas
# de paramètre fetch), incompatible avec l'exigence de tests sans réseau
# (section 1.2 du brief). On réutilise en revanche leurs helpers PURS
# (``home_url_variants``, ``contact_page_urls``) et leur cap de lecture
# (``_PAGE_CAP``) sans rien modifier dans ``website_scraper.py``.
_MAX_CONTACT_PAGES = 2


def _fetch_home_via(fetch: HtmlFetch, url: str) -> "tuple[Optional[str], str]":
    """Home d'un candidat via ``fetch``, en essayant les variantes de schéma/
    www (``website_scraper.home_url_variants``, pure). Renvoie ``(html,
    url_qui_a_répondu)``."""
    for candidate in home_url_variants(url):
        html = fetch(candidate)
        if html is not None:
            return html[:_PAGE_CAP], candidate
    return None, url


def _aggregate_candidate_text(fetch: HtmlFetch, home_html: str, home_url: str) -> str:
    """Texte agrégé home + pages contact/mentions découvertes (cap de pages,
    throttlé via ``fetch`` — chaque tentative consomme un appel réseau)."""
    parts = [home_html]
    fetched = 0
    for curl in contact_page_urls(home_html, home_url):
        if fetched >= _MAX_CONTACT_PAGES:
            break
        page_html = fetch(curl)
        fetched += 1
        if page_html:
            parts.append(page_html[:_PAGE_CAP])
    return " ".join(parts)


def _inspect_candidate(
    fetch: HtmlFetch, url: str, domain: str, name_tokens: List[str], opp: Any,
) -> Dict[str, Any]:
    """Fetch la HOME DU DOMAINE RACINE (pas la page profonde renvoyée par DDG)
    + ses pages contact/mentions, et calcule A et B INDÉPENDAMMENT (traçabilité
    complète même en cas de refus).

    Durcissement 2026-07-14 : le signal de NOM (A) est validé UNIQUEMENT sur la
    home du domaine RACINE (title/h1/og:site_name), pas sur la page profonde que
    DDG a renvoyée — un agrégateur a une home générique (« Trouvez un pro… ») qui
    ne matchera JAMAIS le nom du studio, alors que sa fiche profonde le cite.
    Aucune attribution possible si cette home racine n'a pas répondu
    (``home_alive`` False : domaine mort / timeout / MIME non-HTML)."""
    root_url = "https://" + domain + "/"
    home_html, home_url = _fetch_home_via(fetch, root_url)
    if home_html is None:
        return {"domain": domain, "a_pass": False, "name_signal": None,
                "b_signals": [], "c_pass": False, "title": "", "home_alive": False}

    name_signal = _check_lock_a(name_tokens, extract_identity_markers(home_html), domain)
    aggregated = _aggregate_candidate_text(fetch, home_html, home_url)
    b_signals = _check_lock_b(opp, aggregated)
    c_pass = _check_lock_c(opp, aggregated, b_signals)

    return {
        "domain": domain,
        "a_pass": name_signal is not None,
        "name_signal": name_signal,
        "b_signals": b_signals,
        "c_pass": c_pass,
        "title": _extract_title(home_html),
        "home_alive": True,
    }


# --------------------------------------------------------------------------- #
# Couche de RECHERCHE : liste ORDONNÉE de moteurs (DDG puis repli Bing).       #
# Chaque moteur = constructeur d'URL + parseur pur ; un moteur muet (202/vide/  #
# malformé) fait chuter sur le suivant. Cache de requête : jamais deux fois le  #
# même réseau, et JAMAIS une recherche non servie stockée comme résultat vide.  #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class SearchEngine:
    """Un moteur de recherche public : nom, constructeur d'URL, parseur pur.
    Testable unitairement (URL + parseur indépendants du réseau)."""
    name: str
    url_template: str                         # ``"...{}..."`` -> ``.format(quote_plus(q))``
    parse: "Any"                              # Callable[[Optional[str]], List[str]]

    def url(self, query: str) -> str:
        return self.url_template.format(quote_plus(query))


# Ordre de repli : DuckDuckGo d'abord (statique, parsable), puis Bing HTML si
# DDG « ne sert pas ». Ajouter un moteur = une ligne (URL + parseur).
_ENGINES = (
    SearchEngine("duckduckgo", _DDG_URL, parse_ddg_results),
    SearchEngine("bing", _BING_URL, parse_bing_results),
)


@dataclass
class SearchOutcome:
    """Résultat d'UNE recherche (tous moteurs confondus). ``served`` distingue
    « un moteur a réellement répondu des résultats » (RÉESSAI inutile) d'une
    recherche NON SERVIE (tous muets : 202/défi/vide/malformé) — RÉESSAYABLE."""
    urls: List[str] = field(default_factory=list)
    served: bool = False
    engine: Optional[str] = None  # moteur qui a servi (ou "cache"), None si muet


def _normalize_query(query: str) -> str:
    return _MULTI_SPACE_RE.sub(" ", (query or "").strip().lower())


def _search_cache_key(query: str) -> str:
    return "sitefind:q:" + hashlib.sha1(_normalize_query(query).encode("utf-8")).hexdigest()


def _run_engines(query: str, fetch: HtmlFetch) -> SearchOutcome:
    """Interroge les moteurs DANS L'ORDRE (:data:`_ENGINES`) et rend le PREMIER
    qui sert au moins un résultat. Un moteur « ne sert pas » quand ``fetch``
    rend None (202/défi/MIME/réseau) OU que son parseur ne trouve aucun résultat
    (corps vide/malformé) -> on tente le moteur suivant. Si TOUS sont muets ->
    ``SearchOutcome(served=False)`` (recherche NON SERVIE, réessayable). Aucun
    réseau ici : tout passe par ``fetch`` (injecté en test)."""
    for engine in _ENGINES:
        html = fetch(engine.url(query))
        urls = engine.parse(html)
        if urls:
            return SearchOutcome(urls=urls, served=True, engine=engine.name)
    return SearchOutcome(urls=[], served=False, engine=None)


def _search(
    query: str, session: Session, fetch: HtmlFetch, today: Optional[date],
) -> SearchOutcome:
    """Résultats d'une requête (cascade de moteurs), avec cache (``sitefind:q:``)
    : la même requête normalisée n'est JAMAIS relancée en réseau une 2e fois.

    Le cache ne stocke QUE des recherches réellement SERVIES (``served=True``,
    liste de résultats non vide) : une recherche NON SERVIE (tous moteurs muets
    -> 202/défi/vide/malformé) n'écrit RIEN, sinon une panne anti-bot ponctuelle
    empoisonnerait le cache (et, via :func:`find_site`, verrouillerait la fiche
    en ``no_candidate`` pour +2 mois) au lieu de rester RÉESSAYABLE. Un hit de
    cache est, par construction, une recherche déjà servie (``served=True``)."""
    key = _search_cache_key(query)
    cached = verdict_cache.get(session, key)
    if cached is not None:
        try:
            return SearchOutcome(urls=json.loads(cached.confidence or "[]"),
                                 served=True, engine="cache")
        except (ValueError, TypeError):
            pass
    outcome = _run_engines(_normalize_query(query), fetch)
    if outcome.served:
        verdict_cache.upsert(session, key, verdict="search",
                             confidence=json.dumps(outcome.urls, ensure_ascii=False),
                             profile={}, today=today)
    return outcome


def _build_queries(opp: Any) -> List[str]:
    """Séquence de requêtes moteur (repli, cf. docstring du module) : nom brut +
    ville (ou CP en repli) ; nom + « architecte intérieur » + ville ; dirigeant
    + « architecte intérieur » + ville (si un dirigeant est déclaré)."""
    name = (getattr(opp, "establishment_name", "") or "").strip()
    city = (getattr(opp, "city", "") or "").strip()
    if not city:
        city = _extract_postal_code(getattr(opp, "address", None)) or ""

    queries = [f"{name} {city}".strip()]
    queries.append(f"{name} architecte intérieur {city}".strip())
    dirigeant = _dirigeant_full_name(getattr(opp, "dirigeants", None))
    if dirigeant:
        queries.append(f"{dirigeant} architecte intérieur {city}".strip())
    return queries


# --------------------------------------------------------------------------- #
# Traçabilité + fonction publique principale.                                 #
# --------------------------------------------------------------------------- #

@dataclass
class SiteFindResult:
    """Trace COMPLÈTE d'une tentative de découverte de site pour une fiche —
    chaque signal utilisé pour la décision est documenté (audit)."""
    opp_id: int
    name_raw: str
    queries: List[str] = field(default_factory=list)
    candidates: List[str] = field(default_factory=list)
    website: Optional[str] = None
    # found | locked_out | no_candidate | search_unavailable | error
    #   - search_unavailable : AUCUN moteur n'a servi (202/défi/vide) -> RÉESSAYABLE,
    #     jamais mis en cache. À NE PAS confondre avec no_candidate (moteur servi,
    #     mais 0 candidat propre) — distinction cruciale pour piloter la brique B.
    verdict: str = "no_candidate"
    name_signal: Optional[str] = None  # "A1_content" | "A2_domain" | "C_dirigeant" | None
    corroboration: List[str] = field(default_factory=list)
    inspected: List[Dict[str, Any]] = field(default_factory=list)
    from_cache: bool = False


def _verdict_handle(opp: Any) -> Optional[str]:
    """Clé de cache du VERDICT de la fiche : ``sitefind:opp:<id>``, repli
    ``sitefind:siren:<siren>`` si l'id est absent (fiche non encore commitée)."""
    opp_id = getattr(opp, "id", None)
    if opp_id is not None:
        return f"sitefind:opp:{opp_id}"
    siren = getattr(opp, "siren", None)
    return f"sitefind:siren:{siren}" if siren else None


def find_site(
    opp: Any, session: Session, fetch: HtmlFetch = _polite_get,
    today: Optional[date] = None,
) -> SiteFindResult:
    """Trouve le site PROPRE d'une fiche, ou rien (doctrine VIDE > FAUX).

    Séquence de requêtes en repli (cf. :func:`_build_queries`), chacune servie
    par la cascade de moteurs (DDG puis Bing, cf. :func:`_run_engines`) ;
    candidats filtrés par :func:`own_site` (aucune plateforme/réseau social/
    annuaire), verrou A ET B strict par candidat (cf. docstring du module) — le
    premier candidat qui passe gagne, jamais deux sites attribués. Si AUCUN
    moteur ne sert (tous muets), verdict ``search_unavailable`` (RÉESSAYABLE, non
    caché) plutôt que ``no_candidate``. Cache par fiche (clé
    ``sitefind:opp:``/``sitefind:siren:``) : une fiche déjà tranchée et hors
    fenêtre de revisite n'est PAS re-cherchée (``from_cache=True``)."""
    today = today or date.today()
    name = getattr(opp, "establishment_name", "") or ""
    opp_id = getattr(opp, "id", None)
    handle = _verdict_handle(opp)

    if handle is not None:
        cached = verdict_cache.get(session, handle)
        if cached is not None and not verdict_cache.should_rejudge(session, handle, today=today):
            return SiteFindResult(
                opp_id=opp_id or 0, name_raw=name, website=cached.confidence or None,
                verdict=cached.verdict, from_cache=True,
            )

    result = SiteFindResult(opp_id=opp_id or 0, name_raw=name)
    search_served = False   # >= 1 requête réellement servie par un moteur
    any_muted = False       # >= 1 requête muette (202/défi/vide) -> garde de cache
    try:
        name_tokens = significant_tokens(name)
        seen_domains: set = set()
        for query in _build_queries(opp):
            result.queries.append(query)
            outcome = _search(query, session, fetch, today)
            if outcome.served:
                search_served = True
            else:
                any_muted = True
            for candidate_url in outcome.urls:
                clean = own_site(candidate_url)
                if not clean or _is_directory(clean):
                    continue
                domain = _domain(clean)
                if not domain or domain in seen_domains:
                    continue
                seen_domains.add(domain)
                result.candidates.append(domain)

                info = _inspect_candidate(fetch, clean, domain, name_tokens, opp)
                result.inspected.append(info)
                # Attribution seulement si la home racine est vivante (A/C
                # calculés dessus) ET l'une des deux voies d'identité passe :
                #   - VOIE A+B : nom validé (A) ET corroboration SUFFISANTE (B
                #     non réduite à « ville » seule — durcissement 2026-07-14) ;
                #   - VOIE C : nom COMPLET du dirigeant + signal FORT géo/immat
                #     (calibrage 2026-07-14, raisons sociales abrégées/fusionnées).
                if not info.get("home_alive"):
                    continue
                a_ok = info["a_pass"] and _corroboration_ok(info["b_signals"])
                c_ok = info.get("c_pass", False)
                if a_ok or c_ok:
                    result.website = clean
                    result.verdict = "found"
                    result.name_signal = info["name_signal"] if a_ok else "C_dirigeant"
                    result.corroboration = info["b_signals"]
                    break
            if result.website:
                break

        if result.website is None:
            if result.candidates:
                # >= 1 candidat PROPRE inspecté et rejeté : jugement réel.
                result.verdict = "locked_out"
            elif search_served:
                # Un moteur a servi des résultats, mais AUCUN candidat propre.
                result.verdict = "no_candidate"
            else:
                # AUCUN moteur n'a servi (tous muets 202/défi/vide) : recherche
                # NON SERVIE -> RÉESSAYABLE, jamais figée en "aucun candidat".
                result.verdict = "search_unavailable"
    except Exception:
        result.website = None
        result.verdict = "error"

    # On NE cache PAS :
    #  - "error" : exception transitoire (réseau) ; ne doit pas verrouiller la
    #    fiche (2 mois) — pas un code de cache prévu par la spec §1.8 ;
    #  - "search_unavailable" : recherche non servie (moteurs muets) ; RÉESSAYABLE
    #    par nature, la figer empoisonnerait le cache sur une panne anti-bot ;
    #  - "no_candidate" quand AU MOINS une requête de la séquence a été muette
    #    (``any_muted``) : une requête de repli non servie a pu masquer le vrai
    #    site -> on garde la fiche réessayable plutôt que de la figer.
    skip_cache = result.verdict in ("error", "search_unavailable") or (
        result.verdict == "no_candidate" and any_muted
    )
    if handle is not None and not skip_cache:
        verdict_cache.upsert(session, handle, verdict=result.verdict,
                             confidence=result.website, profile={}, today=today)
    return result
