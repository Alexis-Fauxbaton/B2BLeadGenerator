"""Qualité de contact : niveau (établissement vs décideur) + confiance.

Fonctions pures, testables sans réseau. Cf. docs/contact-tiering-design.md.
- Le NIVEAU d'un contact = source + heuristique sur la valeur (email role-based
  vs nominatif).
- La CONFIANCE (précision d'abord) = à quel point on est sûr que le contact
  appartient bien à CE lead : géo-confirmé > nom+ville > rien.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Optional

# Emails « de rôle » = canal de l'établissement (pas une personne).
ROLE_LOCALPARTS = {
    "contact", "contactez", "info", "infos", "hello", "bonjour", "salut",
    "resa", "reservation", "reservations", "accueil", "commercial", "commerc.",
    "direction", "restaurant", "resto", "bar", "hotel", "cafe", "sav", "rh",
    "recrutement", "presse", "compta", "comptabilite", "admin", "office",
}

# Marqueurs de société mère / structure (pas un local à équiper).
HOLDING_NAME_MARKERS = (
    "holding", "groupe", "invest", "participation", "financiere",
    "food retail", "patrimoine", "gestion",
)

# Mots génériques (type d'établissement, articles, ville) : ignorés pour juger
# qu'un nom CONCORDE avec un autre (on veut les tokens distinctifs).
GENERIC_NAME_WORDS = {
    "le", "la", "les", "du", "de", "des", "et", "aux", "au", "chez", "paris",
    "cafe", "bar", "restaurant", "brasserie", "hotel", "resto", "pizzeria",
    "boulangerie", "traiteur", "bistro", "bistrot", "snack", "food", "the",
}


def _norm(text: Optional[str]) -> str:
    text = (text or "").lower()
    return "".join(
        c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn"
    )


def _tokens(text: Optional[str]) -> set:
    return {t for t in re.split(r"[^a-z0-9]+", _norm(text)) if len(t) > 1}


def is_role_based_email(email: Optional[str]) -> bool:
    """L'email est-il une adresse générique de l'établissement ?"""
    if not email or "@" not in email:
        return False
    local = _norm(email.split("@", 1)[0])
    return local in ROLE_LOCALPARTS


def classify_email(email: Optional[str], decision_maker: Optional[str] = None) -> Optional[str]:
    """Niveau d'un email : 'etablissement' | 'decideur' | None (vide).

    Décideur seulement si signal NOMINATIF fort (prénom.nom, ou un token qui
    matche le nom du dirigeant). Sinon établissement (défaut sûr, précision)."""
    if not email or "@" not in email:
        return None
    if is_role_based_email(email):
        return "etablissement"
    local = _norm(email.split("@", 1)[0])
    parts = [p for p in re.split(r"[._-]+", local) if p]
    # prénom.nom (au moins 2 parties alphabétiques de longueur >= 2)
    if len([p for p in parts if p.isalpha() and len(p) >= 2]) >= 2:
        return "decideur"
    # un token de l'email recoupe le nom du dirigeant connu
    if decision_maker and (_tokens(local) & _tokens(decision_maker)):
        return "decideur"
    return "etablissement"


def looks_like_holding(
    name: Optional[str], naf: Optional[str] = None, activite: Optional[str] = None
) -> bool:
    """Détecte une société mère / holding (à flaguer, pas à équiper)."""
    n = _norm(name)
    if any(m in n for m in HOLDING_NAME_MARKERS):
        return True
    if naf and naf.replace(" ", "").upper().startswith("64.20"):
        return True
    a = _norm(activite)
    # Objet social passe-partout de holding (ex. Lapérouse) :
    if "etablissement de meme nature" in a or "de tout hotel" in a:
        return True
    if "creation" in a and "acquisition" in a and "alienation" in a:
        return True
    return False


def _distinctive_tokens(text: Optional[str]) -> set:
    return {t for t in _tokens(text) if t not in GENERIC_NAME_WORDS and not t.isdigit()}


def names_concordant(enseigne: Optional[str], place_name: Optional[str]) -> bool:
    """Le nom du lieu trouvé recoupe-t-il vraiment notre enseigne ? (tokens
    distinctifs communs). Faux si l'un des deux n'a aucun token distinctif —
    précision d'abord : on ne valide pas un match qu'on ne peut pas corroborer."""
    a = _distinctive_tokens(enseigne)
    b = _distinctive_tokens(place_name)
    if not a or not b:
        return False
    return bool(a & b)


def establishment_confidence(
    match_basis: Optional[str], is_holding: bool, name_ok: bool = True
) -> str:
    """Confiance d'un contact établissement.
    match_basis : 'geo' (lieu Places ≤ seuil du point Sirene) | 'text' (nom+ville)
    | None. Pour un match 'text', on EXIGE en plus la concordance de nom
    (name_ok) — sinon le bon arrondissement ne suffit pas (cas BEAR YTD vs
    Bearsden). Le match 'geo' se suffit à lui-même."""
    if is_holding:
        return "basse"
    if match_basis == "geo":
        return "haute"
    if match_basis == "text" and name_ok:
        return "moyenne"
    return "basse"


def decision_maker_confidence(
    email: Optional[str], decision_maker: Optional[str], is_holding: bool
) -> str:
    """Confiance d'un contact décideur. Précision d'abord : 'haute' seulement si
    l'email est corroboré par le nom du dirigeant ; sinon 'basse'."""
    if is_holding or not email or not decision_maker:
        return "basse"
    local = email.split("@", 1)[0] if "@" in email else email
    if _tokens(local) & _tokens(decision_maker):
        return "haute"
    return "basse"
