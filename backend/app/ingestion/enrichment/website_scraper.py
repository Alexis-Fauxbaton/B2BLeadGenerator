"""Scrape de contacts depuis le site d'un établissement.

Récupère email / instagram / facebook / téléphone en lisant la home + les pages
de contact et mentions légales (légalement obligatoires en France, donc souvent
porteuses d'un email et d'un téléphone).
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlparse

import requests

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; CHR-Signal-Radar/0.1)"}

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
# Emails explicitement déclarés cliquables (fiables, vs placeholders de formulaire).
MAILTO_RE = re.compile(r"mailto:([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})")
INSTA_RE = re.compile(r"instagram\.com/([A-Za-z0-9_.]+)")
FB_RE = re.compile(r"facebook\.com/([A-Za-z0-9_.\-]+)")
TEL_RE = re.compile(r'tel:([+0-9][\d .()-]{6,})')
FR_PHONE_RE = re.compile(r"(?:(?:\+33|0)\s?[1-9])(?:[\s.-]?\d{2}){4}")
# <script>/<style> retirés avant regex téléphone : leur contenu (JSON, valeurs
# CSS type `0.00009999999999999999%` dans une @keyframes) peut sinon matcher
# FR_PHONE_RE et produire un faux numéro (ex: "09 99 99 99 99" extrait d'une
# timing-function CSS, jamais affiché nulle part sur la page).
_NOISE_BLOCK_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.I | re.S)
# Bloc <script>/<style> resté OUVERT en fin de page (page tronquée au cap) :
# sans fermant, _NOISE_BLOCK_RE ne le retire pas et son contenu fuit dans les
# regex téléphone. On coupe tout ce qui suit l'ouvrant orphelin.
_NOISE_TAIL_RE = re.compile(r"<(?:script|style)\b[^>]*>(?:(?!</(?:script|style)).)*$", re.I | re.S)

# Cap de lecture par page. Les builders type Wix inlinent un blob JSON/CSS de
# plusieurs Mo AVANT le contenu utile (cas zephyrinbonal.com : le lien tel: de
# la home est à ~1,8 Mo) — un cap trop bas ampute le vrai numéro. 5 Mo couvre
# ces sites tout en bornant les pages pathologiques.
_PAGE_CAP = 5_000_000


def _strip_noise(html: str) -> str:
    """Retire les blocs <script>/<style>, y compris un bloc final non terminé
    (page tronquée au cap) dont le contenu polluerait les regex téléphone."""
    return _NOISE_TAIL_RE.sub(" ", _NOISE_BLOCK_RE.sub(" ", html))

# Sous-pages utiles à tenter (repli si aucun lien contact découvert sur la home).
CONTACT_PATHS = ["contact", "nous-contacter", "mentions-legales", "mentions-legales/", "legal"]
# Liens de la home menant vraisemblablement à une page contact / mentions légales.
_CONTACT_LINK_RE = re.compile(r"contact|mention|legal", re.I)

# Emails à ignorer (artefacts, libs, exemples, placeholders de formulaire).
EMAIL_JUNK = (
    "sentry", "wixpress", "example.com", "example.org", "domain.com", "email@",
    "your@", "@2x", "@sentry", "godaddy", "wordpress", "squarespace", ".png",
    ".jpg", ".jpeg", ".gif", ".webp", ".svg", "u003e", "name@",
    # placeholders de formulaire (ex: "sophie@email.com", "prenom.nom@...")
    "@email.com", "@exemple", "exemple@", "@votre", "votre@", "prenom", "@adresse",
    "@mail.com", "@test.", "@monsite", "@yourdomain", "@yoursite", "nom@",
)
INSTA_IGNORE = {"p", "reel", "reels", "explore", "accounts", "stories", "tv", "share"}
FB_IGNORE = {"sharer", "tr", "plugins", "dialog", "profile.php", "people"}


def _pick_email(candidates: List[str], site_domain: str) -> Optional[str]:
    found = [e.lower() for e in candidates if not any(j in e.lower() for j in EMAIL_JUNK)]
    if not found:
        return None
    # Préfère un email du même domaine que le site.
    same = [e for e in found if site_domain and site_domain in e.split("@")[-1]]
    return (same or found)[0]


def _clean_emails(html: str, site_domain: str) -> Optional[str]:
    # 1) Emails en `mailto:` = déclarés cliquables -> fiables. 2) Repli texte
    #    libre seulement si aucun mailto (filtre les placeholders de formulaire
    #    type "sophie@email.com" via EMAIL_JUNK).
    return _pick_email(MAILTO_RE.findall(html), site_domain) \
        or _pick_email(EMAIL_RE.findall(html), site_domain)


def _first(matches: List[str], ignore: set) -> Optional[str]:
    for m in matches:
        if m.lower() not in ignore:
            return m
    return None


def _normalize_url(url: str) -> str:
    if not url:
        return ""
    if not url.startswith("http"):
        url = "https://" + url
    return url


def extract_from_html(html: str, site_domain: str = "") -> Dict[str, Optional[str]]:
    """Extraction pure (testable sans réseau) des contacts d'une page HTML."""
    html = _strip_noise(html)
    out: Dict[str, Optional[str]] = {
        "email": _clean_emails(html, site_domain),
        "instagram": _first(INSTA_RE.findall(html), INSTA_IGNORE),
        "facebook": _first(FB_RE.findall(html), FB_IGNORE),
        "phone": None,
    }
    tel = TEL_RE.findall(html)
    if tel:
        out["phone"] = tel[0].strip()
    else:
        fr = FR_PHONE_RE.findall(html)
        if fr:
            out["phone"] = fr[0].strip()
    return out


def scrape_contacts(url: str, max_pages: int = 3, timeout: int = 10) -> Dict[str, Optional[str]]:
    """Renvoie {email, instagram, facebook, phone} trouvés sur le site."""
    result: Dict[str, Optional[str]] = {
        "email": None,
        "instagram": None,
        "facebook": None,
        "phone": None,
    }
    url = _normalize_url(url)
    if not url:
        return result

    site_domain = urlparse(url).netloc.replace("www.", "")
    pages = [url] + [urljoin(url + "/", p) for p in CONTACT_PATHS]

    fetched = 0
    for page in pages:
        if fetched >= max_pages and all(result.values()):
            break
        if fetched >= max_pages:
            # On a épuisé le budget de pages.
            break
        try:
            resp = requests.get(page, headers=HEADERS, timeout=timeout)
            if resp.status_code != 200 or "text/html" not in resp.headers.get("content-type", ""):
                continue
            html = resp.text[:_PAGE_CAP]
        except Exception:
            continue
        fetched += 1

        page_contacts = extract_from_html(html, site_domain)
        for key, value in page_contacts.items():
            if not result[key] and value:
                result[key] = value

    return result


# --------------------------------------------------------------------------- #
# Extraction de TÉLÉPHONE (waterfall « site du lead » — chantier phones).      #
# Un faux numéro = un appel gênant au mauvais commerce : doctrine VIDE > FAUX. #
# --------------------------------------------------------------------------- #

def normalize_fr_phone(raw: Optional[str]) -> Optional[str]:
    """Normalise un numéro FR en format lisible « 0X XX XX XX XX ».

    Renvoie None si le motif n'est pas un numéro FR fixe/mobile plausible
    (précision d'abord : un numéro douteux vaut mieux vide que faux). Gère les
    préfixes +33 / 0033. La chaîne renvoyée sert aussi de clé de déduplication
    (deux écritures du même numéro -> même forme normalisée)."""
    if not raw:
        return None
    plus = raw.strip().startswith("+")
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("0033"):
        digits = "0" + digits[4:]
    elif digits.startswith("33") and (plus or len(digits) == 11):
        digits = "0" + digits[2:]
    # Numéro national : 10 chiffres, commence par 0, second chiffre 1..9.
    if len(digits) != 10 or digits[0] != "0" or digits[1] == "0":
        return None
    return " ".join((digits[0:2], digits[2:4], digits[4:6], digits[6:8], digits[8:10]))


def _distinct(values: List[Optional[str]]) -> List[str]:
    """Numéros non nuls, dédupliqués, ordre d'apparition conservé."""
    out: List[str] = []
    for v in values:
        if v and v not in out:
            out.append(v)
    return out


def extract_phones_from_html(html: str) -> Dict[str, List[str]]:
    """Numéros FR normalisés d'UNE page, séparés par source de confiance :
    ``tel`` = liens ``tel:`` (déclarés cliquables par le site -> les plus
    fiables), ``text`` = numéros repérés dans le texte libre par regex FR.
    Extraction pure (testable sans réseau)."""
    html = _strip_noise(html)
    return {
        "tel": _distinct([normalize_fr_phone(m) for m in TEL_RE.findall(html)]),
        "text": _distinct([normalize_fr_phone(m) for m in FR_PHONE_RE.findall(html)]),
    }


def choose_phone(pages: List[Dict]) -> Optional[str]:
    """Choisit AU PLUS un téléphone, confiance décroissante, VIDE > FAUX.

    Le premier palier non vide DÉCIDE ; s'il est AMBIGU (plusieurs numéros
    distincts) on renvoie None plutôt qu'un numéro au hasard (on n'appelle pas
    le mauvais commerce) — sans redescendre à un palier moins sûr.

    Paliers :
      0. lien ``tel:`` UNIQUE déclaré sur la HOME : le numéro cliquable que
         l'établissement affiche sur sa page d'accueil est son numéro canonique.
         La home étant une source unique et autoritaire, un seul ``tel:`` home
         n'est PAS ambigu, même si des numéros secondaires (agence, fixe d'un
         second point de vente) apparaissent EN PLUS sur les pages contact /
         mentions — ne les laissant plus diluer le signal (cas m2-scene) ;
      1. liens ``tel:`` de toutes les pages (déclaration explicite du site) ;
      2. numéros regex des pages CONTACT / mentions légales ;
      3. numéros regex de la home.

    ``pages`` : liste de ``{'is_contact': bool, 'tel': [...], 'text': [...]}``."""
    tier0 = _distinct([p for pg in pages if not pg.get("is_contact") for p in pg.get("tel", [])])
    tier1 = _distinct([p for pg in pages for p in pg.get("tel", [])])
    tier2 = _distinct([p for pg in pages if pg.get("is_contact") for p in pg.get("text", [])])
    tier3 = _distinct([p for pg in pages if not pg.get("is_contact") for p in pg.get("text", [])])
    for tier in (tier0, tier1, tier2, tier3):
        if tier:
            return tier[0] if len(tier) == 1 else None
    return None


def _fetch_html(url: str, timeout: int) -> Optional[str]:
    """Récupère le HTML d'une page (fail-soft : réseau, statut, type MIME).
    Renvoie None si la page n'est pas un ``text/html`` en 200."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        if resp.status_code != 200 or "text/html" not in resp.headers.get("content-type", ""):
            return None
        return resp.text[:_PAGE_CAP]
    except Exception:
        return None


def contact_page_urls(home_html: str, base_url: str) -> List[str]:
    """Découverte ROBUSTE des pages contact/mentions à sonder pour un site.

    Combine deux sources, dédupliquées (URL absolue, slash final normalisé, home
    exclue), liens de la home d'abord :

      1. les liens ``<a href>`` de la HOME dont l'URL évoque contact / mentions /
         légal — résout relatif ↔ absolu, tolère www ↔ sans-www et /contact ↔
         /contact/ (les redirections HTTP font le reste), et trouve les chemins
         NON standards qu'une liste figée manquerait ;
      2. repli sur les chemins statiques connus (:data:`CONTACT_PATHS`).

    Restreint au domaine du site (jamais un « contactez-nous sur Facebook »).
    Extraction pure (testable sans réseau)."""
    base_host = urlparse(base_url).netloc.replace("www.", "").lower()
    base_key = base_url.rstrip("/")
    urls: List[str] = []
    seen = set()

    def _add(candidate: str) -> None:
        key = candidate.split("#")[0].rstrip("/")
        if not key or key == base_key or key in seen:
            return
        if urlparse(candidate).netloc.replace("www.", "").lower() != base_host:
            return
        seen.add(key)
        urls.append(candidate)

    for href in re.findall(r'<a\b[^>]*?\bhref\s*=\s*["\']([^"\'>]+)["\']', home_html, re.I):
        href = href.strip()
        if href and _CONTACT_LINK_RE.search(href):
            _add(urljoin(base_url + "/", href))
    for path in CONTACT_PATHS:
        _add(urljoin(base_url + "/", path))
    return urls


def scrape_phone(url: str, max_pages: int = 3, timeout: int = 10) -> Optional[str]:
    """Téléphone extrait du SITE d'un lead (home + pages contact/mentions), avec
    découverte robuste des pages contact (cf. :func:`contact_page_urls`) et
    désambiguïsation par palier (cf. :func:`choose_phone`). Fail-soft de bout en
    bout (réseau, statut, type MIME) ; renvoie None si rien de sûr."""
    url = _normalize_url(url)
    if not url:
        return None
    collected: List[Dict] = []
    fetched = 0

    home_html = _fetch_html(url, timeout)
    if home_html is not None:
        fetched += 1
        collected.append({"is_contact": False, **extract_phones_from_html(home_html)})

    for page in contact_page_urls(home_html or "", url):
        if fetched >= max_pages:
            break
        html = _fetch_html(page, timeout)
        if html is None:
            continue
        fetched += 1
        collected.append({"is_contact": True, **extract_phones_from_html(html)})
    return choose_phone(collected)


# --------------------------------------------------------------------------- #
# Email + Instagram + Facebook depuis le SITE PROPRE d'un lead (chantier       #
# enrich_site_contacts). Réutilise les mêmes filtres que ``scrape_contacts``   #
# (mailto prioritaire / EMAIL_JUNK, INSTA_IGNORE, FB_IGNORE) mais AJOUTE la    #
# désambiguïsation multi-comptes Instagram : ``scrape_contacts`` ne garde que  #
# le premier handle trouvé (suffisant pour Places, déjà verrouillé par la     #
# géo) ; ici le site est la SEULE source, donc plusieurs handles distincts    #
# sur le site -> VIDE (on ne devine pas lequel est le bon).                   #
# --------------------------------------------------------------------------- #

def extract_instagram_handles(html: str) -> List[str]:
    """Handles Instagram distincts d'UNE page, filtrés (``INSTA_IGNORE``),
    dédupliqués (insensible à la casse, ordre d'apparition conservé).
    Extraction pure (testable sans réseau)."""
    html = _strip_noise(html)
    out: List[str] = []
    seen_lower = set()
    for m in INSTA_RE.findall(html):
        low = m.lower()
        if low in INSTA_IGNORE or low in seen_lower:
            continue
        seen_lower.add(low)
        out.append(m)
    return out


def choose_instagram(pages_handles: List[List[str]]) -> Optional[str]:
    """Choisit AU PLUS un handle Instagram sur l'ensemble du site (toutes pages
    confondues), doctrine VIDE > FAUX : si plusieurs handles DISTINCTS
    apparaissent, on ne sait pas lequel est celui du lead -> vide plutôt qu'un
    choix arbitraire."""
    distinct: List[str] = []
    seen_lower = set()
    for handles in pages_handles:
        for h in handles:
            low = h.lower()
            if low not in seen_lower:
                seen_lower.add(low)
                distinct.append(h)
    return distinct[0] if len(distinct) == 1 else None


def scrape_site_contacts(url: str, max_pages: int = 3, timeout: int = 10) -> Dict[str, Optional[str]]:
    """Email / Instagram / Facebook depuis le SITE PROPRE d'un lead (home + pages
    contact/mentions légales). Ne renvoie PAS de téléphone (domaine exclusif de
    :func:`scrape_phone` / la passe ``enrich_phones``). Fail-soft de bout en
    bout ; renvoie des champs à None si rien de sûr n'est trouvé."""
    result: Dict[str, Optional[str]] = {"email": None, "instagram": None, "facebook": None}
    url = _normalize_url(url)
    if not url:
        return result

    site_domain = urlparse(url).netloc.replace("www.", "")
    pages = [url] + [urljoin(url + "/", p) for p in CONTACT_PATHS]

    fetched = 0
    insta_per_page: List[List[str]] = []
    for page in pages:
        if fetched >= max_pages:
            break
        try:
            resp = requests.get(page, headers=HEADERS, timeout=timeout)
            if resp.status_code != 200 or "text/html" not in resp.headers.get("content-type", ""):
                continue
            html = resp.text[:_PAGE_CAP]
        except Exception:
            continue
        fetched += 1

        clean_html = _strip_noise(html)
        if not result["email"]:
            result["email"] = _clean_emails(clean_html, site_domain)
        if not result["facebook"]:
            result["facebook"] = _first(FB_RE.findall(clean_html), FB_IGNORE)
        insta_per_page.append(extract_instagram_handles(html))

    result["instagram"] = choose_instagram(insta_per_page)
    return result
