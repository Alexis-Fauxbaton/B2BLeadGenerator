"""Auth légère : hachage de mot de passe (bcrypt) + cookie de session signé
(itsdangerous) + dépendance `get_current_user` OPTIONNELLE.

Principe « soft » : l'app doit continuer de marcher sans aucun compte (Alexis
aujourd'hui). `get_current_user` renvoie donc `None` — JAMAIS un 401 — quand il
n'y a pas de session valide. Quand une session existe, l'auteur/les assignations
se remplissent automatiquement côté serveur (on ne fait pas confiance à un
`author` fourni par le client).
"""
import os
import secrets
from typing import Optional

import bcrypt
from fastapi import Depends, HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlmodel import Session

from .database import get_session
from .models import User

# Nom du cookie de session (httpOnly, signé). Court et neutre.
SESSION_COOKIE_NAME = "chr_session"

# Durée de vie de la session : 30 jours (les closers restent loggés).
SESSION_MAX_AGE = 60 * 60 * 24 * 30

# Fail-soft : si `SESSION_SECRET` n'est pas configuré dans l'environnement, on
# génère un secret ÉPHÉMÈRE au démarrage du process. L'app fonctionne (le login
# marche dans la session courante), mais les cookies n'y survivent PAS à un
# redémarrage — documenté dans .env.example, à renseigner en prod.
_EPHEMERAL_SECRET = secrets.token_urlsafe(48)


def _secret() -> str:
    return os.getenv("SESSION_SECRET") or _EPHEMERAL_SECRET


def _serializer() -> URLSafeTimedSerializer:
    # Lu à l'appel (et non figé à l'import) pour respecter un SESSION_SECRET posé
    # après coup (tests / .env chargé tardivement).
    return URLSafeTimedSerializer(_secret(), salt="chr-session-v1")


# --- Mots de passe (bcrypt) ---------------------------------------------------


def hash_password(password: str) -> str:
    """Hache un mot de passe avec bcrypt (sel intégré). Renvoie un str stockable."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Vérifie un mot de passe contre son hash. Fail-soft : un hash corrompu
    renvoie False (jamais d'exception qui casserait le login)."""
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# --- Cookie de session signé --------------------------------------------------


def create_session_token(user_id: int) -> str:
    """Jeton signé (et daté) portant l'id utilisateur, à poser dans le cookie."""
    return _serializer().dumps({"uid": user_id})


def read_session_token(token: str) -> Optional[dict]:
    """Décode un jeton. Renvoie le payload dict, ou None si signature invalide /
    expirée (fail-soft : un cookie trafiqué ne casse rien, il ne loggue juste
    personne)."""
    if not token:
        return None
    try:
        return _serializer().loads(token, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None


# --- Dépendance FastAPI OPTIONNELLE -------------------------------------------


def get_current_user(
    request: Request,
    session: Session = Depends(get_session),
) -> Optional[User]:
    """Utilisateur courant d'après le cookie de session, ou None. NE LÈVE JAMAIS
    de 401 : les routes existantes restent ouvertes tant que personne n'est
    loggé. Quand une session valide existe, renvoie l'objet User frais de la DB."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    data = read_session_token(token) if token else None
    if not data:
        return None
    user_id = data.get("uid")
    if user_id is None:
        return None
    return session.get(User, user_id)


def require_admin_soft(current_user) -> None:
    """Garde « admin, mais SOFT » : conforme à l'auth soft, l'action reste LIBRE
    tant que personne n'est loggé (Alexis aujourd'hui, sans compte). Dès qu'une
    session existe, elle doit être admin — un closer loggé se voit refuser (403).

    `isinstance(..., User)` : appelée en direct dans les tests, `current_user`
    peut être la sentinelle Depends (traitée comme « pas de session »)."""
    if isinstance(current_user, User) and current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Réservé à l'administrateur")
