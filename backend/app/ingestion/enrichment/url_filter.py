"""Filtre : qu'est-ce qu'un VRAI site d'établissement ?

Un lien d'agrégateur (linktr.ee/linktree), de carte/partage (maps.app.goo.gl,
goo.gl), un profil social (facebook.com, instagram.com) ou un raccourcisseur
(bit.ly) n'est PAS un site d'établissement — mieux vaut un champ `website` vide
qu'un faux site (le propriétaire prospecte par email/DM : un mauvais lien =
prospection dans le vide). Cf. audit-enrichissement-report.md (cause n°1).
"""
from __future__ import annotations

from typing import Optional

# Sous-chaînes (hôte/domaine) qui disqualifient une URL comme "site" propre.
# goo.gl couvre déjà maps.app.goo.gl (sous-chaîne) mais on le liste pour la
# lisibilité ; facebook.com couvre facebook.com/share.
NON_SITE_HOSTS = (
    "linktr.ee", "linktree",
    "maps.app.goo.gl", "goo.gl",
    "facebook.com", "instagram.com",
    "bit.ly",
)


def is_real_website(url: Optional[str]) -> bool:
    """True si `url` ressemble à un vrai site (pas un agrégateur/social/carte)."""
    if not url:
        return False
    u = url.strip().lower()
    if not u:
        return False
    return not any(bad in u for bad in NON_SITE_HOSTS)


def clean_website(url: Optional[str]) -> Optional[str]:
    """Renvoie l'URL (nettoyée des espaces) si c'est un vrai site, sinon None."""
    if not url:
        return None
    u = url.strip()
    return u if is_real_website(u) else None
