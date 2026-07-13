"""CLI de création de compte : `python -m app.create_user`.

Sert à créer le PREMIER admin (le patron) puis les closers.

  python -m app.create_user --admin --name "Alexis" --email alexis@ambient.home
  python -m app.create_user --name "Marie" --email marie@ambient.home --password secret

Sans --password, le mot de passe est demandé interactivement (masqué). La
logique de création est isolée dans `create_user()` pour être testée en session
mémoire (l'entreprise a ~5 150 leads réels : aucun test ne touche la vraie DB).
"""
import argparse
import getpass
import sys
from typing import Optional

from sqlmodel import Session, select

from .models import User
from .security import hash_password


def create_user(
    session: Session,
    *,
    name: str,
    email: str,
    password: str,
    admin: bool = False,
) -> User:
    """Crée et persiste un compte (mot de passe haché). Lève ValueError si
    l'email existe déjà ou si le mot de passe est vide."""
    email = email.strip().lower()
    if not password:
        raise ValueError("Mot de passe vide.")
    if session.exec(select(User).where(User.email == email)).first() is not None:
        raise ValueError(f"Un compte existe déjà pour {email}.")

    user = User(
        name=name.strip(),
        email=email,
        password_hash=hash_password(password),
        role="admin" if admin else "closer",
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.create_user",
        description="Crée un compte (admin ou closer) pour CHR Signal Radar.",
    )
    parser.add_argument("--admin", action="store_true", help="Crée un compte admin (patron).")
    parser.add_argument("--name", required=True, help="Nom affiché du compte.")
    parser.add_argument("--email", required=True, help="Email (identifiant de login, unique).")
    parser.add_argument("--password", help="Mot de passe (sinon demandé interactivement).")
    args = parser.parse_args(argv)

    # Import tardif : ne charge la DB que pour l'exécution réelle (pas à l'import
    # du module, ce qui garde les tests en session mémoire découplés).
    from .database import engine, init_db

    init_db()  # garantit que la table `users` existe.

    password = args.password
    if not password:
        password = getpass.getpass("Mot de passe : ")
        confirm = getpass.getpass("Confirmer : ")
        if password != confirm:
            print("Les mots de passe ne correspondent pas.", file=sys.stderr)
            return 1

    with Session(engine) as session:
        try:
            user = create_user(
                session,
                name=args.name,
                email=args.email,
                password=password,
                admin=args.admin,
            )
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1

    print(
        f"Compte créé : {user.name} <{user.email}> "
        f"(role={user.role}, id={user.id})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
