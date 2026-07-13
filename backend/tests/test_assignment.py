"""Assignation des leads + vues (patron /activite, « Mes leads »/« Mes relances »),
TDD. Aucun réseau, DB mémoire (l'entreprise a ~5 150 leads réels : aucun test ne
touche la vraie DB).

Couvre :
- migration/modèle `Opportunity.assigned_to` (défaut None) + PATCH assignment ;
- garde admin SOFT (libre sans session, 403 pour un closer loggé, ok admin) ;
- filtre `assigned=me|none|<nom>` sur /api/opportunities et /api/followups
  (`me` résout via la session ; sans session -> aucun résultat) ;
- badge /followups/count respecte `assigned=me` ;
- vue /api/activite : jointure nom de fiche, filtre jour (défaut aujourd'hui) +
  auteur, compteurs par closer, garde admin.
"""
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.assignment import apply_assigned_filter
from app.create_user import create_user
from app.models import ContactActivity, Opportunity, User
from app.routes.activite import get_activity_journal
from app.security import SESSION_COOKIE_NAME, require_admin_soft
from fastapi import HTTPException
from sqlmodel import select


# --- Fixtures (calquées sur test_auth) ----------------------------------------


def _memory_engine():
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


def _seed_user(engine, *, name, email=None, password="secret123", admin=False):
    with Session(engine) as s:
        return create_user(
            s, name=name, email=email or f"{name.lower()}@ambient.home",
            password=password, admin=admin,
        )


def _login(client, email, password="secret123"):
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200
    return r


def _mk_opp(session, *, name="Studio", assigned_to=None, follow_up=None,
            population="architecte", status="non_contacte"):
    o = Opportunity(
        establishment_name=name, establishment_type="architecte d'intérieur",
        city="Paris", address="", main_signal="prescripteur actif",
        detection_date=date(2026, 7, 1), estimated_timing="",
        population=population, status=status,
        assigned_to=assigned_to, next_follow_up_date=follow_up,
    )
    session.add(o)
    session.commit()
    session.refresh(o)
    return o


# --- Modèle + PATCH assignment ------------------------------------------------


def test_assigned_to_defaults_none(engine):
    with Session(engine) as s:
        o = _mk_opp(s)
        assert o.assigned_to is None


def test_patch_assignment_sets_and_clears_without_session(engine, client):
    with Session(engine) as s:
        opp = _mk_opp(s)
    # Sans session : SOFT -> libre (Alexis aujourd'hui, sans compte).
    r = client.patch(f"/api/opportunities/{opp.id}/assignment",
                     json={"assigned_to": "Marie"})
    assert r.status_code == 200
    assert r.json()["assigned_to"] == "Marie"
    # Désassignation (null).
    r = client.patch(f"/api/opportunities/{opp.id}/assignment",
                     json={"assigned_to": None})
    assert r.status_code == 200
    assert r.json()["assigned_to"] is None


def test_patch_assignment_404(client):
    r = client.patch("/api/opportunities/999999/assignment",
                     json={"assigned_to": "Marie"})
    assert r.status_code == 404


def test_patch_assignment_admin_logged_ok(engine, client):
    _seed_user(engine, name="Alexis", email="alexis@ambient.home", admin=True)
    with Session(engine) as s:
        opp = _mk_opp(s)
    _login(client, "alexis@ambient.home")
    r = client.patch(f"/api/opportunities/{opp.id}/assignment",
                     json={"assigned_to": "Marie"})
    assert r.status_code == 200
    assert r.json()["assigned_to"] == "Marie"


def test_patch_assignment_closer_logged_403(engine, client):
    _seed_user(engine, name="Marie", email="marie@ambient.home")  # closer
    with Session(engine) as s:
        opp = _mk_opp(s)
    _login(client, "marie@ambient.home")
    r = client.patch(f"/api/opportunities/{opp.id}/assignment",
                     json={"assigned_to": "Marie"})
    assert r.status_code == 403


def test_require_admin_soft_unit():
    # Pas de session (None ou sentinelle) -> autorisé.
    require_admin_soft(None)
    # Closer loggé -> 403.
    closer = User(name="M", email="m@a.co", password_hash="x", role="closer")
    with pytest.raises(HTTPException) as exc:
        require_admin_soft(closer)
    assert exc.value.status_code == 403
    # Admin -> autorisé.
    require_admin_soft(User(name="A", email="a@a.co", password_hash="x", role="admin"))


# --- Filtre assigned : helper pur ---------------------------------------------


def test_apply_assigned_filter_none_unassigned(engine):
    with Session(engine) as s:
        _mk_opp(s, name="Libre", assigned_to=None)
        _mk_opp(s, name="Prise", assigned_to="Marie")
        q = apply_assigned_filter(select(Opportunity), "none", None)
        names = {o.establishment_name for o in s.exec(q).all()}
        assert names == {"Libre"}


def test_apply_assigned_filter_by_name(engine):
    with Session(engine) as s:
        _mk_opp(s, name="A", assigned_to="Marie")
        _mk_opp(s, name="B", assigned_to="Jean")
        q = apply_assigned_filter(select(Opportunity), "Jean", None)
        names = {o.establishment_name for o in s.exec(q).all()}
        assert names == {"B"}


def test_apply_assigned_filter_me_without_session_is_empty(engine):
    with Session(engine) as s:
        _mk_opp(s, name="A", assigned_to="Marie")
        q = apply_assigned_filter(select(Opportunity), "me", None)
        assert s.exec(q).all() == []


def test_apply_assigned_filter_me_resolves_current_user(engine):
    marie = User(name="Marie", email="m@a.co", password_hash="x")
    with Session(engine) as s:
        _mk_opp(s, name="Sienne", assigned_to="Marie")
        _mk_opp(s, name="Autre", assigned_to="Jean")
        q = apply_assigned_filter(select(Opportunity), "me", marie)
        names = {o.establishment_name for o in s.exec(q).all()}
        assert names == {"Sienne"}


def test_apply_assigned_filter_empty_no_op(engine):
    with Session(engine) as s:
        _mk_opp(s, name="A")
        _mk_opp(s, name="B", assigned_to="Marie")
        q = apply_assigned_filter(select(Opportunity), None, None)
        assert len(s.exec(q).all()) == 2


# --- /api/opportunities?assigned=... (bout en bout) ---------------------------


def test_opportunities_assigned_me_via_session(engine, client):
    _seed_user(engine, name="Marie", email="marie@ambient.home")
    with Session(engine) as s:
        _mk_opp(s, name="Sienne", assigned_to="Marie")
        _mk_opp(s, name="Autre", assigned_to="Jean")
        _mk_opp(s, name="Libre", assigned_to=None)
    _login(client, "marie@ambient.home")
    r = client.get("/api/opportunities?assigned=me")
    assert r.status_code == 200
    names = {o["establishment_name"] for o in r.json()}
    assert names == {"Sienne"}


def test_opportunities_assigned_none_for_admin(engine, client):
    with Session(engine) as s:
        _mk_opp(s, name="Sienne", assigned_to="Marie")
        _mk_opp(s, name="Libre", assigned_to=None)
    r = client.get("/api/opportunities?assigned=none")
    names = {o["establishment_name"] for o in r.json()}
    assert names == {"Libre"}


def test_opportunities_assigned_serialized_in_list(engine, client):
    with Session(engine) as s:
        _mk_opp(s, name="Prise", assigned_to="Marie")
    r = client.get("/api/opportunities")
    assert r.json()[0]["assigned_to"] == "Marie"


# --- /api/followups?assigned=... + /count -------------------------------------


def test_followups_assigned_me_via_session(engine, client):
    _seed_user(engine, name="Marie", email="marie@ambient.home")
    today = date.today()
    with Session(engine) as s:
        _mk_opp(s, name="Sienne", assigned_to="Marie", follow_up=today)
        _mk_opp(s, name="Autre", assigned_to="Jean", follow_up=today)
    _login(client, "marie@ambient.home")
    r = client.get("/api/followups?assigned=me")
    assert r.status_code == 200
    body = r.json()
    all_names = {
        o["establishment_name"]
        for bucket in ("en_retard", "aujourdhui", "cette_semaine")
        for o in body[bucket]
    }
    assert all_names == {"Sienne"}


def test_followups_count_respects_assigned_me(engine, client):
    _seed_user(engine, name="Marie", email="marie@ambient.home")
    today = date.today()
    with Session(engine) as s:
        _mk_opp(s, name="Sienne", assigned_to="Marie", follow_up=today)
        _mk_opp(s, name="Autre", assigned_to="Jean", follow_up=today)
        _mk_opp(s, name="Libre", assigned_to=None, follow_up=today)
    _login(client, "marie@ambient.home")
    r = client.get("/api/followups/count?assigned=me")
    assert r.status_code == 200
    assert r.json()["total"] == 1  # seulement la relance de Marie
    # Sans filtre : les trois.
    assert client.get("/api/followups/count").json()["total"] == 3


# --- /api/activite (vue patron) -----------------------------------------------


def _mk_activity(session, opp_id, *, type="appel", author=None, note=None, when=None):
    a = ContactActivity(
        opportunity_id=opp_id, type=type, author=author, note=note,
        created_at=when or datetime.utcnow(),
    )
    session.add(a)
    session.commit()
    session.refresh(a)
    return a


def test_activite_journal_joins_opportunity_name_and_defaults_today(engine, client):
    with Session(engine) as s:
        opp = _mk_opp(s, name="Studio Alpha")
        _mk_activity(s, opp.id, author="Marie", note="Répondu")
    r = client.get("/api/activite")
    assert r.status_code == 200
    body = r.json()
    assert body["day"] == date.today().isoformat()
    assert len(body["activities"]) == 1
    entry = body["activities"][0]
    assert entry["opportunity_name"] == "Studio Alpha"
    assert entry["author"] == "Marie"


def test_activite_filters_by_day(engine, client):
    yesterday = datetime.utcnow() - timedelta(days=1)
    with Session(engine) as s:
        opp = _mk_opp(s)
        _mk_activity(s, opp.id, author="Marie", note="hier", when=yesterday)
        _mk_activity(s, opp.id, author="Jean", note="aujourd'hui")
    # Défaut = aujourd'hui : ne voit que l'activité du jour.
    today_body = client.get("/api/activite").json()
    assert {a["author"] for a in today_body["activities"]} == {"Jean"}
    # Jour explicite = hier.
    y = (date.today() - timedelta(days=1)).isoformat()
    y_body = client.get(f"/api/activite?day={y}").json()
    assert {a["author"] for a in y_body["activities"]} == {"Marie"}


def test_activite_filters_by_author(engine, client):
    with Session(engine) as s:
        opp = _mk_opp(s)
        _mk_activity(s, opp.id, author="Marie")
        _mk_activity(s, opp.id, author="Jean")
    body = client.get("/api/activite?author=Marie").json()
    assert {a["author"] for a in body["activities"]} == {"Marie"}


def test_activite_counts_per_author_over_whole_day(engine, client):
    with Session(engine) as s:
        opp = _mk_opp(s)
        _mk_activity(s, opp.id, author="Marie")
        _mk_activity(s, opp.id, author="Marie")
        _mk_activity(s, opp.id, author="Jean")
    # Le filtre auteur NE réduit PAS les compteurs (répartition patron intacte).
    body = client.get("/api/activite?author=Marie").json()
    counts = {c["author"]: c["count"] for c in body["counts"]}
    assert counts == {"Marie": 2, "Jean": 1}


def test_activite_invalid_day_422(client):
    r = client.get("/api/activite?day=pas-une-date")
    assert r.status_code == 422


def test_activite_admin_guard(engine, client):
    # Closer loggé -> 403.
    _seed_user(engine, name="Marie", email="marie@ambient.home")
    _login(client, "marie@ambient.home")
    assert client.get("/api/activite").status_code == 403
    # Logout -> soft (sans session) -> ok.
    client.post("/api/auth/logout")
    assert client.get("/api/activite").status_code == 200


def test_activite_unit_direct_call(engine):
    # Appel direct (current_user garde la sentinelle Depends -> soft ok).
    with Session(engine) as s:
        opp = _mk_opp(s, name="Studio Beta")
        _mk_activity(s, opp.id, author="Marie", note="Pas de réponse")
        journal = get_activity_journal(session=s)
        assert journal.day == date.today()
        assert journal.activities[0].opportunity_name == "Studio Beta"
        assert journal.counts[0].author == "Marie"
