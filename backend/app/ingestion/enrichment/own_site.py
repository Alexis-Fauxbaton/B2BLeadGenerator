"""Garde « site propre » : est-ce le SITE de l'établissement, pas un profil
social ou un portail ?

Partagé entre ``enrich_phones.py`` et ``enrich_site_contacts.py`` : les deux
passes ne doivent scraper QUE le site propre du lead — un profil TikTok/
LinkedIn/Houzz… n'appartient pas au lead, y chercher un contact renverrait
celui de n'importe qui (doctrine VIDE > FAUX).
"""
from __future__ import annotations

from typing import Optional

from .url_filter import is_real_website

# Hôtes qui, EN PLUS de ceux déjà écartés par ``is_real_website`` (linktr.ee /
# facebook / instagram / goo.gl / bit.ly), ne sont PAS le site propre d'un lead.
NON_OWN_SITE_HOSTS = (
    "tiktok.com", "linkedin.com", "houzz.", "youtube.com", "youtu.be",
    "twitter.com", "x.com", "pinterest.", "wa.me",
)


def own_site(url: Optional[str]) -> Optional[str]:
    """URL seulement si c'est le SITE PROPRE du lead. Un profil social / portail
    (TikTok, LinkedIn, Houzz…) n'est pas son site : on n'y scrape rien.
    Réutilise ``is_real_website`` (linktree/FB/IG/raccourcisseurs) et complète."""
    if not is_real_website(url):
        return None
    low = url.lower()
    if any(host in low for host in NON_OWN_SITE_HOSTS):
        return None
    return url.strip()
