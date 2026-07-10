"""Qualité de contact : niveau (établissement vs décideur) + confiance.

Fonctions pures, testables sans réseau. Cf. docs/contact-tiering-design.md.

Principe (volontairement SIMPLE — précision d'abord) : un contact établissement
n'est de confiance que si le match Places est GÉO-confirmé (lieu ≈ point du
lead). Tout match "par nom" est trop peu fiable pour un commerce frais sans
empreinte (homonymes) -> on ne le montre pas, on dit "à trouver", et la
vérification fine du flou est déléguée à l'agent (cf. roadmap Phase 2). On
n'empile donc PAS de rustines (concordance de nom, plausibilité d'avis, indicatif
téléphonique…) : une seule règle, géo ou rien.
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


# Format email raisonnable (local@domaine.tld). Ancré (^…$) : rejette un domaine
# nu sans '@' (id 319) ou un numéro de téléphone dans le champ email (id 404/424).
EMAIL_RE = re.compile(r"^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$")


def normalize_email(value: Optional[str]) -> Optional[str]:
    """Email minuscule s'il a un format VALIDE, sinon None. Précision d'abord :
    un champ vide vaut mieux qu'un faux (domaine sans '@', numéro de téléphone
    collé dans l'email…). Point d'entrée unique de validation avant stockage."""
    if not value:
        return None
    v = value.strip().lower()
    return v if EMAIL_RE.match(v) else None


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


def establishment_confidence(match_basis: Optional[str]) -> str:
    """Confiance d'un contact établissement. Précision d'abord : 'haute'
    UNIQUEMENT si le match Places est géo-confirmé (lieu ≈ point Sirene du lead).
    Tout le reste (match par nom/ville, ou pas de match) -> 'basse' = "à trouver".
    """
    return "haute" if match_basis == "geo" else "basse"


def decision_maker_confidence(email: Optional[str], decision_maker: Optional[str]) -> str:
    """Confiance d'un contact décideur. Précision d'abord : 'haute' seulement si
    l'email est corroboré par le nom du dirigeant ; sinon 'basse'."""
    if not email or not decision_maker:
        return "basse"
    local = email.split("@", 1)[0] if "@" in email else email
    if _tokens(local) & _tokens(decision_maker):
        return "haute"
    return "basse"
