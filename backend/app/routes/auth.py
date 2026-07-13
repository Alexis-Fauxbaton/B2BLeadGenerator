"""Routes d'authentification légère : /api/auth/{login,logout,me,users}.

Cookie de session signé httpOnly (voir app/security.py). SOFT partout : /me
renvoie l'utilisateur courant OU null (200), jamais 401 — le frontend s'en sert
juste pour savoir s'il faut afficher l'état loggé.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlmodel import Session, select

from ..database import get_session
from ..models import User
from ..schemas import LoginRequest, UserPublic, UserRead
from ..security import (
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE,
    create_session_token,
    get_current_user,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=UserRead)
def login(
    payload: LoginRequest,
    response: Response,
    session: Session = Depends(get_session),
):
    """Authentifie par email + mot de passe et pose le cookie de session signé.
    401 (message générique) si l'email est inconnu OU le mot de passe faux — on
    ne distingue pas les deux cas (pas d'énumération de comptes)."""
    email = payload.email.strip().lower()
    user = session.exec(select(User).where(User.email == email)).first()
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Identifiants invalides")

    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=create_session_token(user.id),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return user


@router.post("/logout")
def logout(response: Response):
    """Efface le cookie de session (idempotent : marche même sans session)."""
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me", response_model=Optional[UserRead])
def me(current_user: Optional[User] = Depends(get_current_user)):
    """Utilisateur courant, ou null si personne n'est loggé (200, jamais 401)."""
    return current_user


@router.get("/users", response_model=List[UserPublic])
def list_users(session: Session = Depends(get_session)):
    """Liste des comptes (id + nom SEULEMENT), triés par nom. Ouvert (pas de
    garde admin) : sert le dropdown « Assigné à » sur la fiche. email/role ne
    sont PAS exposés ici (énumération de comptes) — /me reste la seule route à
    les renvoyer, pour l'utilisateur courant uniquement."""
    return session.exec(select(User).order_by(User.name)).all()
