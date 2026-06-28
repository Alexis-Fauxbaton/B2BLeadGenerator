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
INSTA_RE = re.compile(r"instagram\.com/([A-Za-z0-9_.]+)")
FB_RE = re.compile(r"facebook\.com/([A-Za-z0-9_.\-]+)")
TEL_RE = re.compile(r'tel:([+0-9][\d .()-]{6,})')
FR_PHONE_RE = re.compile(r"(?:(?:\+33|0)\s?[1-9])(?:[\s.-]?\d{2}){4}")

# Sous-pages utiles à tenter.
CONTACT_PATHS = ["contact", "nous-contacter", "mentions-legales", "mentions-legales/", "legal"]

# Emails à ignorer (artefacts, libs, exemples).
EMAIL_JUNK = (
    "sentry", "wixpress", "example.com", "example.org", "domain.com", "email@",
    "your@", "@2x", "@sentry", "godaddy", "wordpress", "squarespace", ".png",
    ".jpg", ".jpeg", ".gif", ".webp", ".svg", "u003e", "name@",
)
INSTA_IGNORE = {"p", "reel", "reels", "explore", "accounts", "stories", "tv", "share"}
FB_IGNORE = {"sharer", "tr", "plugins", "dialog", "profile.php", "people"}


def _clean_emails(html: str, site_domain: str) -> Optional[str]:
    found = []
    for e in EMAIL_RE.findall(html):
        el = e.lower()
        if any(j in el for j in EMAIL_JUNK):
            continue
        found.append(el)
    if not found:
        return None
    # Préfère un email du même domaine que le site.
    same = [e for e in found if site_domain and site_domain in e.split("@")[-1]]
    return (same or found)[0]


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
            html = resp.text[:500_000]  # cap taille
        except Exception:
            continue
        fetched += 1

        page_contacts = extract_from_html(html, site_domain)
        for key, value in page_contacts.items():
            if not result[key] and value:
                result[key] = value

    return result
