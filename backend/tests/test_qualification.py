"""Monitoring des résultats de qualification (patron) + batch last-issues, TDD.
Aucun réseau, DB mémoire.

Couvre :
- GET /api/activite/stats : KPIs (tentatives/joignabilité/volume d'appels/
  réponses email+DM), par closer, par canal, top raisons de KO, volume
  d'appels/jour, presets de période (today/7j/30j) + dates libres, garde admin
  SOFT (mêmes gardes que le journal) ;
- GET /api/opportunities/last-issues : batch (une requête), dérivé à la volée,
  jamais persisté sur la fiche ;
- GET /api/meta : la taxonomie de qualification (source de vérité backend) est
  servie au frontend ;
- non-régression : aucune de ces routes de LECTURE n'écrit jamais sur une
  fiche (ni statut, ni next_action, ni aucun autre champ).
"""
from datetime import date, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models import ContactActivity, Opportunity
from app.routes.activite import get_qualif_stats
from app.routes.activities import get_last_issues


# --- Fixtures (calquées sur test_assignment / test_auth) -----------------------


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
    from fastapi.testclient import TestClient

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


def _opp(session, name="Studio"):
    o = Opportunity(
        establishment_name=name, establishment_type="architecte d'intérieur",
        city="Paris", address="", main_signal="prescripteur actif",
        detection_date=date(2026, 7, 1), estimated_timing="",
        population="architecte",
    )
    session.add(o)
    session.commit()
    session.refresh(o)
    return o


def _act(session, opp_id, *, type="appel", issue=None, raison=None, detail=None,
          author=None, when=None):
    a = ContactActivity(
        opportunity_id=opp_id, type=type, issue=issue, raison=raison,
        detail=detail or [], author=author,
        created_at=when or datetime.utcnow(),
    )
    session.add(a)
    session.commit()
    session.refresh(a)
    return a


# --- GET /api/activite/stats : KPIs --------------------------------------------


def test_stats_kpis_tentatives_and_joignabilite():
    with Session(_memory_engine()) as s:
        opp = _opp(s)
        _act(s, opp.id, issue="joint", raison="interesse", author="Marie")
        _act(s, opp.id, issue="joint", raison="pas_interesse", author="Marie")
        _act(s, opp.id, issue="pas_joint", raison="pas_de_reponse", author="Jean")
        _act(s, opp.id, issue="ko", raison="mauvais_numero", author="Jean")
        # Émission sans résultat -> n'est PAS une tentative (issue NULL).
        _act(s, opp.id, type="email", issue=None, author="Marie")

        stats = get_qualif_stats(session=s, period="today")
        assert stats.kpis.tentatives == 4
        assert stats.kpis.joignabilite == pytest.approx(2 / 4)


def test_stats_kpis_volume_appels_counts_all_calls_regardless_of_issue():
    """Le volume d'appels (rythme) compte TOUS les appels, résultat connu ou
    pas -- distinct des « tentatives » qui exigent un résultat (`issue`)."""
    with Session(_memory_engine()) as s:
        opp = _opp(s)
        _act(s, opp.id, type="appel", issue="joint", raison="interesse")
        _act(s, opp.id, type="appel", issue=None)  # improbable mais toléré
        _act(s, opp.id, type="email", issue="joint", raison="interesse")

        stats = get_qualif_stats(session=s, period="today")
        assert stats.kpis.volume_appels == 2


def test_stats_kpis_reponses_email_dm_requires_a_result():
    with Session(_memory_engine()) as s:
        opp = _opp(s)
        _act(s, opp.id, type="email", issue="joint", raison="interesse")
        _act(s, opp.id, type="dm_insta", issue="pas_joint", raison="pas_de_reponse")
        _act(s, opp.id, type="email", issue=None)  # émission, pas une réponse

        stats = get_qualif_stats(session=s, period="today")
        assert stats.kpis.reponses_email_dm == 2


def test_stats_joignabilite_is_none_without_any_attempt():
    """Aucune tentative sur la période -> None (pas 0 %), pour distinguer
    « zéro joignabilité » de « pas de données »."""
    with Session(_memory_engine()) as s:
        _opp(s)  # aucune activité
        stats = get_qualif_stats(session=s, period="today")
        assert stats.kpis.tentatives == 0
        assert stats.kpis.joignabilite is None


# --- Par closer / par canal -----------------------------------------------------


def test_stats_by_closer():
    with Session(_memory_engine()) as s:
        opp = _opp(s)
        _act(s, opp.id, issue="joint", raison="interesse", author="Marie")
        _act(s, opp.id, issue="ko", raison="mauvais_numero", author="Marie")
        _act(s, opp.id, issue="joint", raison="interesse", author="Jean")

        stats = get_qualif_stats(session=s, period="today")
        by_closer = {c.closer: c for c in stats.by_closer}
        assert by_closer["Marie"].tentatives == 2
        assert by_closer["Marie"].joints == 1
        assert by_closer["Marie"].joignabilite == pytest.approx(0.5)
        assert by_closer["Jean"].tentatives == 1
        assert by_closer["Jean"].joignabilite == 1.0


def test_stats_by_channel():
    with Session(_memory_engine()) as s:
        opp = _opp(s)
        _act(s, opp.id, type="appel", issue="joint", raison="interesse")
        _act(s, opp.id, type="appel", issue="pas_joint", raison="repondeur")
        _act(s, opp.id, type="email", issue="joint", raison="interesse")

        stats = get_qualif_stats(session=s, period="today")
        by_channel = {c.type: c for c in stats.by_channel}
        assert by_channel["appel"].tentatives == 2
        assert by_channel["appel"].joignabilite == pytest.approx(0.5)
        assert by_channel["email"].tentatives == 1
        assert by_channel["email"].joignabilite == 1.0


# --- Top raisons de KO -----------------------------------------------------------


def test_stats_top_ko_reasons_sorted_desc_max_five():
    with Session(_memory_engine()) as s:
        opp = _opp(s)
        for _ in range(3):
            _act(s, opp.id, issue="ko", raison="mauvais_numero")
        for _ in range(2):
            _act(s, opp.id, issue="ko", raison="ferme")
        _act(s, opp.id, issue="ko", raison="ne_plus_contacter")
        _act(s, opp.id, issue="joint", raison="interesse")  # pas KO -> exclu

        stats = get_qualif_stats(session=s, period="today")
        assert [r.raison for r in stats.top_ko_reasons] == [
            "mauvais_numero", "ferme", "ne_plus_contacter",
        ]
        assert stats.top_ko_reasons[0].count == 3


# --- Volume d'appels par jour ----------------------------------------------------


def test_stats_daily_call_volume_fills_zero_for_missing_days():
    today = date.today()
    with Session(_memory_engine()) as s:
        opp = _opp(s)
        _act(s, opp.id, type="appel", issue="joint", raison="interesse",
             when=datetime.combine(today, datetime.min.time()) + timedelta(hours=9))
        _act(s, opp.id, type="appel", issue="ko", raison="ferme",
             when=datetime.combine(today, datetime.min.time()) + timedelta(hours=14))
        # Un appel il y a 2 jours (dans la fenêtre 7j).
        _act(s, opp.id, type="appel", issue="joint", raison="interesse",
             when=datetime.combine(today - timedelta(days=2), datetime.min.time()))

        stats = get_qualif_stats(session=s, period="7j")
        assert len(stats.daily_call_volume) == 7
        by_day = {d.day: d.count for d in stats.daily_call_volume}
        assert by_day[today] == 2
        assert by_day[today - timedelta(days=2)] == 1
        assert by_day[today - timedelta(days=1)] == 0  # comblé à 0


# --- Périodes : presets + dates libres -------------------------------------------


def test_stats_period_today_excludes_yesterday():
    today = date.today()
    with Session(_memory_engine()) as s:
        opp = _opp(s)
        _act(s, opp.id, issue="joint", raison="interesse",
             when=datetime.combine(today - timedelta(days=1), datetime.min.time()))
        stats = get_qualif_stats(session=s, period="today")
        assert stats.kpis.tentatives == 0


def test_stats_period_30j():
    today = date.today()
    with Session(_memory_engine()) as s:
        opp = _opp(s)
        _act(s, opp.id, issue="joint", raison="interesse",
             when=datetime.combine(today - timedelta(days=20), datetime.min.time()))
        _act(s, opp.id, issue="joint", raison="interesse",
             when=datetime.combine(today - timedelta(days=45), datetime.min.time()))  # hors 30j

        stats = get_qualif_stats(session=s, period="30j")
        assert stats.kpis.tentatives == 1
        assert stats.period_start == today - timedelta(days=29)
        assert stats.period_end == today


def test_stats_free_dates_take_priority_over_period_preset():
    with Session(_memory_engine()) as s:
        opp = _opp(s)
        _act(s, opp.id, issue="joint", raison="interesse", when=datetime(2026, 6, 1, 10))
        stats = get_qualif_stats(
            session=s, period="today", start="2026-06-01", end="2026-06-01",
        )
        assert stats.kpis.tentatives == 1
        assert stats.period_start == date(2026, 6, 1)
        assert stats.period_end == date(2026, 6, 1)


def test_stats_invalid_date_422():
    with Session(_memory_engine()) as s:
        with pytest.raises(HTTPException) as exc:
            get_qualif_stats(session=s, start="pas-une-date")
        assert exc.value.status_code == 422


def test_stats_end_before_start_422():
    with Session(_memory_engine()) as s:
        with pytest.raises(HTTPException) as exc:
            get_qualif_stats(session=s, start="2026-07-10", end="2026-07-01")
        assert exc.value.status_code == 422


def test_stats_period_too_wide_422():
    """Borne défensive sur une plage de dates libres : au-delà de
    `_MAX_PERIOD_DAYS`, `daily_call_volume` (boucle jour par jour) produirait
    une réponse démesurée -> 422 plutôt qu'un scan/payload sans limite."""
    with Session(_memory_engine()) as s:
        with pytest.raises(HTTPException) as exc:
            get_qualif_stats(session=s, start="2020-01-01", end="2026-07-13")
        assert exc.value.status_code == 422


def test_stats_period_within_bound_still_works():
    """Non-régression : une plage large mais sous la borne reste acceptée."""
    with Session(_memory_engine()) as s:
        stats = get_qualif_stats(session=s, start="2024-01-01", end="2026-01-01")
        assert stats.period_start == date(2024, 1, 1)
        assert stats.period_end == date(2026, 1, 1)


# --- Garde admin SOFT -------------------------------------------------------------


def test_stats_admin_guard(engine, client):
    from app.create_user import create_user

    with Session(engine) as s:
        create_user(s, name="Marie", email="marie@ambient.home",
                     password="secret123", admin=False)
    r = client.post("/api/auth/login", json={"email": "marie@ambient.home",
                                              "password": "secret123"})
    assert r.status_code == 200
    assert client.get("/api/activite/stats").status_code == 403
    client.post("/api/auth/logout")
    assert client.get("/api/activite/stats").status_code == 200


# --- 100 % lecture : aucune écriture sur les fiches -------------------------------


def test_stats_never_writes_any_opportunity_field():
    with Session(_memory_engine()) as s:
        opp = _opp(s)
        opp.updated_at = datetime(2020, 1, 1)
        s.add(opp)
        s.commit()
        _act(s, opp.id, issue="ko", raison="mauvais_numero")

        get_qualif_stats(session=s, period="7j")

        s.refresh(opp)
        assert opp.updated_at == datetime(2020, 1, 1)  # STRICTEMENT inchangé
        assert opp.status == "non_contacte"


def test_last_issues_never_writes_any_opportunity_field():
    with Session(_memory_engine()) as s:
        opp = _opp(s)
        opp.updated_at = datetime(2020, 1, 1)
        s.add(opp)
        s.commit()
        _act(s, opp.id, issue="ko", raison="mauvais_numero")

        get_last_issues(ids=str(opp.id), session=s)

        s.refresh(opp)
        assert opp.updated_at == datetime(2020, 1, 1)


# --- GET /api/opportunities/last-issues : batch, dérivé, jamais persisté --------


def test_last_issues_returns_most_recent_qualified_activity_per_opp():
    with Session(_memory_engine()) as s:
        a = _opp(s, name="A")
        b = _opp(s, name="B")
        base = datetime(2026, 7, 10, 9, 0, 0)
        _act(s, a.id, issue="pas_joint", raison="repondeur", when=base)
        _act(s, a.id, issue="ko", raison="mauvais_numero", when=base + timedelta(hours=1))
        # émission sans résultat : ignorée par last-issues (issue NULL).
        _act(s, b.id, type="email", issue=None, when=base + timedelta(hours=2))
        _act(s, b.id, type="email", issue="joint", raison="interesse", when=base + timedelta(hours=3))

        result = get_last_issues(ids=f"{a.id},{b.id}", session=s)
        assert result[a.id].issue == "ko"
        assert result[a.id].raison == "mauvais_numero"
        assert result[b.id].issue == "joint"


def test_last_issues_omits_opps_without_any_qualified_activity():
    with Session(_memory_engine()) as s:
        a = _opp(s, name="A")
        b = _opp(s, name="B")
        _act(s, a.id, issue="joint", raison="interesse")
        # b n'a aucune activité qualifiée -> absent du résultat.
        result = get_last_issues(ids=f"{a.id},{b.id}", session=s)
        assert a.id in result
        assert b.id not in result


def test_last_issues_empty_ids_returns_empty_dict():
    with Session(_memory_engine()) as s:
        assert get_last_issues(ids="", session=s) == {}


def test_last_issues_invalid_ids_422():
    with Session(_memory_engine()) as s:
        with pytest.raises(HTTPException) as exc:
            get_last_issues(ids="pas-un-id", session=s)
        assert exc.value.status_code == 422


def test_last_issues_route_not_shadowed_by_opportunity_id_route(engine, client):
    """Régression d'ordre de routage : `GET /api/opportunities/last-issues`
    (chemin littéral) doit répondre 200 avec le bon payload -- PAS un 422/404
    de `GET /api/opportunities/{opportunity_id}` qui tenterait de convertir
    "last-issues" en entier."""
    with Session(engine) as s:
        opp = _opp(s, name="Studio")
        opp_id = opp.id  # capturé AVANT le commit de _act (qui expire `opp`)
        _act(s, opp_id, issue="joint", raison="interesse")

    r = client.get(f"/api/opportunities/last-issues?ids={opp_id}")
    assert r.status_code == 200
    body = r.json()
    assert body[str(opp_id)]["issue"] == "joint"

    # La route dynamique reste, elle, fonctionnelle pour un VRAI id.
    r2 = client.get(f"/api/opportunities/{opp_id}")
    assert r2.status_code == 200


# --- GET /api/meta : taxonomie servie au frontend --------------------------------


def test_meta_exposes_qualif_taxonomy(client):
    r = client.get("/api/meta")
    assert r.status_code == 200
    body = r.json()
    taxo = body["qualif_taxonomy"]
    assert taxo["issues"] == ["joint", "pas_joint", "ko"]
    assert taxo["raisons"]["appel"]["joint"][0] == "rdv_pris"  # issue reine, en tête
    assert taxo["raisons"]["appel"]["ko"] == [
        "mauvais_numero", "ferme", "hors_cible", "ne_plus_contacter",
    ]
    assert "deja_fournisseur" in taxo["details"]
