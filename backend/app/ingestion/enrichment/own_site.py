"""Garde « site propre » : est-ce le SITE de l'établissement, pas un profil
social ou un portail ?

Partagé entre ``enrich_phones.py`` et ``enrich_site_contacts.py`` : les deux
passes ne doivent scraper QUE le site propre du lead — un profil TikTok/
LinkedIn/Houzz… n'appartient pas au lead, y chercher un contact renverrait
celui de n'importe qui (doctrine VIDE > FAUX).
"""
from __future__ import annotations

import re
from typing import Optional

from .url_filter import is_real_website

# Hôtes qui, EN PLUS de ceux déjà écartés par ``is_real_website`` (linktr.ee /
# facebook / instagram / goo.gl / bit.ly), ne sont PAS le site propre d'un lead.
NON_OWN_SITE_HOSTS = (
    "tiktok.com", "linkedin.com", "houzz.", "youtube.com", "youtu.be",
    "twitter.com", "x.com", "pinterest.", "wa.me",
)

# Annuaires d'entreprises / agrégateurs / cartes / plateformes de devis : leurs
# pages republient nom + ville + CP + SIREN EXACTS de N'IMPORTE QUELLE
# entreprise -> une corroboration géo/immatriculation y passe TRIVIALEMENT à
# tort. Ce n'est JAMAIS le site PROPRE du lead (doctrine VIDE > FAUX). Liste
# PARTAGÉE (``enrich_phones`` / ``enrich_site_contacts`` / ``site_finder``),
# étendue au gate du 2026-07-14 (6 faux positifs sur 15) : 118000/118712,
# le-site-de, prosmaison, hexagone-architecture (devis), hoodspot, mappy…
# Sous-chaînes d'hôte (comparaison ``host in url.lower()``).
DIRECTORY_HOSTS = (
    "118000.fr", "118712.fr", "le-site-de.com", "prosmaison.fr",
    "hexagone-architecture.fr", "hoodspot", "mappy.com", "mappy.fr",
    "pappers.fr", "societe.com", "societe.ninja", "societe-info.fr",
    "verif.com", "infogreffe", "kompass", "manageo.fr", "b-reputation.com",
    "dnb.com", "score3.fr", "bilansgratuits.fr", "ellisphere.fr",
    "corporama.com", "indexa.fr", "annuaire-entreprises", "entreprises.lefigaro.fr",
    "pagesjaunes", "yelp.fr", "yelp.com", "tripadvisor.fr", "tripadvisor.com",
    "trustpilot.com",
)

# Motifs d'URL de FICHE GÉNÉRÉE par un agrégateur (SIREN/SIRET ou identifiant
# interne dans le chemin) : présents même sur des hôtes pas encore listés
# ci-dessus. Observés au gate : ``prosmaison.fr/entreprise-43435829700076``,
# ``118000.fr/e_C0101327518``, ``le-site-de.com/novea-home-coueron_33582.html``.
# Calibrage 2026-07-14 (rééquilibrage — moins d'exclusions à tort de pages
# légitimes) :
#   - ``e_C\d+`` -> ``/e_C\d+`` : ancré en début de SEGMENT de chemin (l'id de
#     fiche 118000 est ``/e_C<id>``), plus le ``…e_C…`` fortuit au milieu d'un
#     mot ;
#   - ``_\d{4,}\.html`` -> ``_\d{5,}\.html`` : les identifiants le-site-de font
#     >= 5 chiffres (``_251396``, ``_33582``, ``_46787``) ; un slug daté légitime
#     ``…_2024.html`` (4 chiffres, une année) n'est PLUS pris pour un annuaire.
DIRECTORY_URL_RE = re.compile(
    r"/entreprise-\d{9,14}"   # /entreprise-<SIREN|SIRET>
    r"|/e_C\d+"               # identifiant de fiche 118000 (segment /e_C<id>)
    r"|_\d{5,}\.html",        # slug_<id>.html (le-site-de…), id >= 5 chiffres
    re.I,
)


def is_directory(url: Optional[str]) -> bool:
    """True si l'URL est un annuaire d'entreprises / agrégateur / carte / devis
    (hôte de :data:`DIRECTORY_HOSTS` OU motif d'URL de fiche générée
    :data:`DIRECTORY_URL_RE`) — jamais le site PROPRE du lead, même s'il en cite
    le nom/SIREN/adresse."""
    if not url:
        return False
    low = url.lower()
    if any(host in low for host in DIRECTORY_HOSTS):
        return True
    return bool(DIRECTORY_URL_RE.search(url))


def own_site(url: Optional[str]) -> Optional[str]:
    """URL seulement si c'est le SITE PROPRE du lead. Un profil social / portail
    (TikTok, LinkedIn, Houzz…) ou un annuaire/agrégateur (Pappers, societe.com,
    118000, prosmaison…) n'est pas son site : on n'y scrape rien. Réutilise
    ``is_real_website`` (linktree/FB/IG/raccourcisseurs) et complète."""
    if not is_real_website(url):
        return None
    low = url.lower()
    if any(host in low for host in NON_OWN_SITE_HOSTS):
        return None
    if is_directory(url):
        return None
    return url.strip()
