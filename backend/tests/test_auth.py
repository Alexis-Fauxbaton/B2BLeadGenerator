"""Auth légère (comptes closers Ambient Home), TDD. Aucun réseau, DB mémoire.

Couvre : hash bcrypt (roundtrip + rejet), jeton de session signé (roundtrip +
falsification), CLI create_user (hash, rôle, garde email unique / mdp vide),
routes /api/auth/{login,logout,me} via TestClient (cookie posé/effacé, 401 ko,
me soft), dépendance get_current_user optionnelle (cookie absent/trafiqué ->
None), et remplissage AUTO de `author` (la session PRIME sur le body).
"""
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlalchemy.pool import StaticPool

from app.create_user import create_user
from app.models import Opportunity, User
from app.routes.activities import add_activity
from app.schemas import ContactActivityCreate
from app.security import (
    SESSION_COOKIE_NAME,
    create_session_token,
    get_current_user,
    hash_password,
    read_session_token,
    verify_password,
)


# --- Fixtures : engine mémoire partagé + app avec get_session overridé --------


def _memory_engine():
    # StaticPool + une seule connexion : le même schéma/données vus par l'app et
    # le test (sinon `sqlite://` recrée une base vide par connexion).
    e = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(e)
    return e


@pytest.fixture
def engine():
    return _memory_engine()


@pytest.fixture
def client(engine):
    from app.database import get_session
    from app.main import app

    def _override():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = _override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _seed_user(engine, *, name="Marie", email="marie@ambient.home",
               password="secret123", admin=False):
    with Session(engine) as s:
        return create_user(s, name=name, email=email, password=password, admin=admin)


# --- bcrypt -------------------------------------------------------------------


def test_hash_password_roundtrip():
    h = hash_password("s3cret!")
    assert h != "s3cret!"  # jamais en clair
    assert verify_password("s3cret!", h) is True
    assert verify_password("mauvais", h) is False


def test_verify_password_failsoft_on_garbage_hash():
    assert verify_password("x", "") is False
    assert verify_password("x", "pas-un-hash-bcrypt") is False


# --- Jeton de session signé ---------------------------------------------------


def test_session_token_roundtrip():
    token = create_session_token(42)
    assert read_session_token(token) == {"uid": 42}


def test_session_token_tampered_returns_none():
    token = create_session_token(7)
    assert read_session_token(token + "x") is None
    assert read_session_token("n'importe.quoi") is None
    assert read_session_token("") is None


# --- CLI create_user ----------------------------------------------------------


def test_create_user_hashes_and_defaults_closer(engine):
    with Session(engine) as s:
        u = create_user(s, name="Marie", email="Marie@Ambient.Home", password="pw")
        assert u.id is not None
        assert u.role == "closer"
        assert u.email == "marie@ambient.home"  # normalisé lowercase
        assert u.password_hash != "pw" and verify_password("pw", u.password_hash)


def test_create_user_admin_flag(engine):
    with Session(engine) as s:
        u = create_user(s, name="Alexis", email="a@a.co", password="pw", admin=True)
        assert u.role == "admin"


def test_create_user_rejects_duplicate_email(engine):
    with Session(engine) as s:
        create_user(s, name="A", email="dup@a.co", password="pw")
    with Session(engine) as s:
        with pytest.raises(ValueError):
            create_user(s, name="B", email="dup@a.co", password="pw")


def test_create_user_rejects_empty_password(engine):
    with Session(engine) as s:
        with pytest.raises(ValueError):
            create_user(s, name="A", email="x@a.co", password="")


# --- /api/auth/login ----------------------------------------------------------


def test_login_ok_sets_cookie_and_returns_user(engine, client):
    _seed_user(engine, email="marie@ambient.home", password="secret123")
    r = client.post("/api/auth/login",
                    json={"email": "marie@ambient.home", "password": "secret123"})
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == "marie@ambient.home" and body["role"] == "closer"
    assert "password_hash" not in body  # jamais exposé
    assert SESSION_COOKIE_NAME in r.cookies


def test_login_is_case_insensitive_on_email(engine, client):
    _seed_user(engine, email="marie@ambient.home", password="secret123")
    r = client.post("/api/auth/login",
                    json={"email": "  MARIE@Ambient.Home ", "password": "secret123"})
    assert r.status_code == 200


def test_login_wrong_password_401_no_cookie(engine, client):
    _seed_user(engine, email="marie@ambient.home", password="secret123")
    r = client.post("/api/auth/login",
                    json={"email": "marie@ambient.home", "password": "FAUX"})
    assert r.status_code == 401
    assert SESSION_COOKIE_NAME not in r.cookies


def test_login_unknown_email_401(engine, client):
    r = client.post("/api/auth/login",
                    json={"email": "personne@a.co", "password": "x"})
    assert r.status_code == 401


# --- /api/auth/me + logout ----------------------------------------------------


def test_me_without_session_returns_null(client):
    r = client.get("/api/auth/me")
    assert r.status_code == 200  # soft : jamais 401
    assert r.json() is None


def test_me_with_session_returns_user(engine, client):
    _seed_user(engine, email="marie@ambient.home", password="secret123")
    client.post("/api/auth/login",
                json={"email": "marie@ambient.home", "password": "secret123"})
    r = client.get("/api/auth/me")
    assert r.status_code == 200
    assert r.json()["email"] == "marie@ambient.home"


def test_me_with_tampered_cookie_returns_null(client):
    client.cookies.set(SESSION_COOKIE_NAME, "cookie.trafique")
    r = client.get("/api/auth/me")
    assert r.status_code == 200
    assert r.json() is None


def test_logout_clears_session(engine, client):
    _seed_user(engine, email="marie@ambient.home", password="secret123")
    client.post("/api/auth/login",
                json={"email": "marie@ambient.home", "password": "secret123"})
    assert client.get("/api/auth/me").json() is not None
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").json() is None


# --- GET /api/auth/users (dropdown d'assignation) ------------------------------


def test_list_users_sorted_by_name_no_password_hash(engine, client):
    _seed_user(engine, name="Zoe", email="zoe@ambient.home", admin=True)
    _seed_user(engine, name="Awa", email="awa@ambient.home")
    body = client.get("/api/auth/users").json()
    assert [u["name"] for u in body] == ["Awa", "Zoe"]
    assert all("password_hash" not in u for u in body)


def test_list_users_exposes_only_id_and_name(engine, client):
    """Pas d'énumération de comptes : email/role NE sont PAS renvoyés par
    GET /api/auth/users (seul /me les renvoie, pour l'utilisateur courant)."""
    _seed_user(engine, name="Awa", email="awa@ambient.home", admin=True)
    body = client.get("/api/auth/users").json()
    assert set(body[0].keys()) == {"id", "name"}


def test_list_users_open_without_session(client):
    assert client.get("/api/auth/users").status_code == 200


# --- Dépendance get_current_user (unitaire) -----------------------------------


class _FakeRequest:
    def __init__(self, cookies):
        self.cookies = cookies


def test_get_current_user_none_without_cookie(engine):
    with Session(engine) as s:
        assert get_current_user(_FakeRequest({}), s) is None


def test_get_current_user_resolves_valid_cookie(engine):
    u = _seed_user(engine)
    with Session(engine) as s:
        token = create_session_token(u.id)
        got = get_current_user(_FakeRequest({SESSION_COOKIE_NAME: token}), s)
        assert got is not None and got.id == u.id


# --- author AUTO : la session PRIME sur le body -------------------------------


def _opp(session):
    o = Opportunity(
        establishment_name="Studio", establishment_type="architecte d'intérieur",
        city="Paris", address="", main_signal="prescripteur actif",
        detection_date=__import__("datetime").date(2026, 7, 1),
        estimated_timing="", population="architecte",
    )
    session.add(o)
    session.commit()
    session.refresh(o)
    return o


def test_activity_author_from_session_overrides_body(engine):
    marie = _seed_user(engine, name="marie")
    with Session(engine) as s:
        opp = _opp(s)
        # Body tente une AUTRE identité -> ignorée : la session prime.
        act = add_activity(
            opp.id,
            ContactActivityCreate(type="appel", author="hacker"),
            s,
            current_user=marie,
        )
        assert act.author == "marie"


def test_activity_author_falls_back_to_body_without_session(engine):
    with Session(engine) as s:
        opp = _opp(s)
        # Pas de session (appel direct : current_user garde son défaut Depends)
        # -> le body est retenu (app ouverte sans compte).
        act = add_activity(
            opp.id, ContactActivityCreate(type="appel", author="anonyme"), s
        )
        assert act.author == "anonyme"


def test_activity_author_none_when_no_session_no_body(engine):
    with Session(engine) as s:
        opp = _opp(s)
        act = add_activity(opp.id, ContactActivityCreate(type="note", note="RAS"), s)
        assert act.author is None
