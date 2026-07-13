"""Suivi de contact SOBRE (closers Ambient Home), TDD. Aucun réseau.

Couvre : création d'activité + touche `updated_at`, validation du type, tri
desc + pagination du journal, journal AUTO du changement de statut ('statut',
ancien -> nouveau), prochaine action (pose + effacement ensemble), buckets
« À relancer » (en_retard / aujourdhui / cette_semaine) + exclusion gagne/perdu,
compteur du badge, et migrations (colonne next_action + table contact_activities).
"""
from datetime import date, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import ContactActivity, Opportunity
from app.routes.activities import add_activity, list_activities, set_next_action
from app.routes.followups import get_follow_ups, get_follow_ups_count
from app.routes.opportunities import update_status
from app.schemas import ContactActivityCreate, NextActionUpdate, StatusUpdate


def _engine():
    e = create_engine("sqlite://")
    SQLModel.metadata.create_all(e)
    return e


def _opp(session, name="Studio", status="non_contacte", follow_up=None,
         score=5, pop="architecte"):
    o = Opportunity(
        establishment_name=name, establishment_type="architecte d'intérieur",
        city="Paris", address="", main_signal="prescripteur actif",
        detection_date=date(2026, 7, 1), estimated_timing="",
        opportunity_score=score, status=status, population=pop,
        next_follow_up_date=follow_up,
    )
    session.add(o)
    session.commit()
    session.refresh(o)
    return o


# --- POST /activities ---------------------------------------------------------


def test_add_activity_persists_and_touches_updated_at():
    with Session(_engine()) as s:
        opp = _opp(s)
        opp.updated_at = datetime(2020, 1, 1)
        s.add(opp)
        s.commit()

        act = add_activity(opp.id, ContactActivityCreate(type="appel", note="RAS"), s)
        assert act.id is not None
        assert act.type == "appel" and act.note == "RAS"
        assert act.opportunity_id == opp.id

        s.refresh(opp)
        assert opp.updated_at > datetime(2020, 1, 1)  # l'activité fait vivre la fiche
        # Le statut n'est JAMAIS touché par un geste rapide.
        assert opp.status == "non_contacte"


def test_add_activity_rejects_unknown_type():
    with Session(_engine()) as s:
        opp = _opp(s)
        with pytest.raises(HTTPException) as exc:
            add_activity(opp.id, ContactActivityCreate(type="pigeon"), s)
        assert exc.value.status_code == 422


def test_add_activity_404_on_missing_opp():
    with Session(_engine()) as s:
        with pytest.raises(HTTPException) as exc:
            add_activity(999, ContactActivityCreate(type="note"), s)
        assert exc.value.status_code == 404


def test_add_activity_note_type_with_inline_text():
    with Session(_engine()) as s:
        opp = _opp(s)
        act = add_activity(opp.id, ContactActivityCreate(type="note", note="relancé par mail"), s)
        assert act.type == "note" and act.note == "relancé par mail"


# --- GET /activities : tri desc + pagination ----------------------------------


def test_list_activities_desc_and_pagination():
    with Session(_engine()) as s:
        opp = _opp(s)
        base = datetime(2026, 7, 10, 9, 0, 0)
        for i in range(3):
            s.add(ContactActivity(
                opportunity_id=opp.id, type="appel", note=f"a{i}",
                created_at=base + timedelta(hours=i),
            ))
        s.commit()

        rows = list_activities(opp.id, s)
        # plus récent d'abord
        assert [r.note for r in rows] == ["a2", "a1", "a0"]

        # pagination légère
        page = list_activities(opp.id, s, limit=2, offset=0)
        assert [r.note for r in page] == ["a2", "a1"]
        page2 = list_activities(opp.id, s, limit=2, offset=2)
        assert [r.note for r in page2] == ["a0"]


def test_list_activities_404_on_missing_opp():
    with Session(_engine()) as s:
        with pytest.raises(HTTPException) as exc:
            list_activities(999, s)
        assert exc.value.status_code == 404


# --- PATCH status : journal AUTO d'une activité 'statut' ----------------------


def test_status_change_auto_journals_statut_activity():
    with Session(_engine()) as s:
        opp = _opp(s, status="non_contacte")
        update_status(opp.id, StatusUpdate(status="contacte"), s)

        acts = s.exec(select(ContactActivity).where(
            ContactActivity.opportunity_id == opp.id)).all()
        assert len(acts) == 1
        assert acts[0].type == "statut"
        assert acts[0].note == "non_contacte -> contacte"


def test_status_unchanged_does_not_journal():
    # Pas le fouilli : re-poser le MÊME statut (ex. juste (re)planifier une
    # relance) ne crée pas d'activité 'statut'.
    with Session(_engine()) as s:
        opp = _opp(s, status="contacte")
        update_status(
            opp.id,
            StatusUpdate(status="contacte", next_follow_up_date=date(2026, 8, 1)),
            s,
        )
        acts = s.exec(select(ContactActivity).where(
            ContactActivity.opportunity_id == opp.id)).all()
        assert acts == []


# --- PUT /next-action : pose + effacement ensemble ----------------------------


def test_set_next_action_sets_both():
    with Session(_engine()) as s:
        opp = _opp(s)
        out = set_next_action(
            opp.id,
            NextActionUpdate(next_action="Rappeler le gérant", next_follow_up_date=date(2026, 7, 20)),
            s,
        )
        assert out.next_action == "Rappeler le gérant"
        assert out.next_follow_up_date == date(2026, 7, 20)


def test_set_next_action_clears_both_when_empty():
    with Session(_engine()) as s:
        opp = _opp(s, follow_up=date(2026, 7, 20))
        opp.next_action = "vieux texte"
        s.add(opp)
        s.commit()

        out = set_next_action(opp.id, NextActionUpdate(), s)  # {} => efface les deux
        assert out.next_action is None
        assert out.next_follow_up_date is None


# --- GET /followups : buckets + exclusion + compteur --------------------------


def test_followups_buckets():
    today = date.today()
    with Session(_engine()) as s:
        _opp(s, name="Retard", follow_up=today - timedelta(days=2))
        _opp(s, name="Aujourdhui", follow_up=today)
        _opp(s, name="Semaine", follow_up=today + timedelta(days=3))
        _opp(s, name="PlusTard", follow_up=today + timedelta(days=30))  # exclu
        _opp(s, name="SansDate", follow_up=None)  # exclu

        buckets = get_follow_ups(session=s, population="architecte")
        assert [o.establishment_name for o in buckets.en_retard] == ["Retard"]
        assert [o.establishment_name for o in buckets.aujourdhui] == ["Aujourdhui"]
        assert [o.establishment_name for o in buckets.cette_semaine] == ["Semaine"]
        # next_action est exposé sur chaque fiche listée.
        assert hasattr(buckets.en_retard[0], "next_action")


def test_followups_excludes_gagne_perdu():
    today = date.today()
    with Session(_engine()) as s:
        _opp(s, name="Gagne", status="gagne", follow_up=today - timedelta(days=1))
        _opp(s, name="Perdu", status="perdu", follow_up=today)
        _opp(s, name="Actif", status="relance", follow_up=today)

        buckets = get_follow_ups(session=s, population="architecte")
        names = ([o.establishment_name for o in buckets.en_retard]
                 + [o.establishment_name for o in buckets.aujourdhui]
                 + [o.establishment_name for o in buckets.cette_semaine])
        assert names == ["Actif"]


def test_followups_ordered_by_due_date():
    today = date.today()
    with Session(_engine()) as s:
        _opp(s, name="J-1", follow_up=today - timedelta(days=1))
        _opp(s, name="J-5", follow_up=today - timedelta(days=5))
        buckets = get_follow_ups(session=s, population="architecte")
        # le plus en retard d'abord (échéance croissante)
        assert [o.establishment_name for o in buckets.en_retard] == ["J-5", "J-1"]


def test_followups_population_filter():
    today = date.today()
    with Session(_engine()) as s:
        _opp(s, name="Arc", pop="architecte", follow_up=today)
        _opp(s, name="Chr", pop="chr", follow_up=today)
        arc = get_follow_ups(session=s, population="architecte")
        assert [o.establishment_name for o in arc.aujourdhui] == ["Arc"]
        allpop = get_follow_ups(session=s, population="")  # vide = toutes
        assert sorted(o.establishment_name for o in allpop.aujourdhui) == ["Arc", "Chr"]


def test_followups_count():
    today = date.today()
    with Session(_engine()) as s:
        _opp(s, name="R1", follow_up=today - timedelta(days=2))
        _opp(s, name="R2", follow_up=today - timedelta(days=1))
        _opp(s, name="A1", follow_up=today)
        _opp(s, name="S1", follow_up=today + timedelta(days=4))
        _opp(s, name="Loin", follow_up=today + timedelta(days=40))  # exclu

        c = get_follow_ups_count(session=s, population="architecte")
        assert c.en_retard == 2
        assert c.aujourdhui == 1
        assert c.cette_semaine == 1
        assert c.total == 4


# --- Migrations ---------------------------------------------------------------


def test_migration_adds_next_action_column(tmp_path):
    from sqlalchemy import create_engine as ce, inspect, text
    import app.database as db

    url = f"sqlite:///{tmp_path/'legacy.db'}"
    old = ce(url)
    with old.begin() as conn:
        conn.execute(text("CREATE TABLE opportunities (id INTEGER PRIMARY KEY, "
                          "establishment_name VARCHAR, establishment_type VARCHAR, "
                          "city VARCHAR, address VARCHAR, main_signal VARCHAR, "
                          "detection_date DATE, estimated_timing VARCHAR)"))
    old.dispose()

    orig_engine, orig_url = db.engine, db.DATABASE_URL
    db.engine, db.DATABASE_URL = ce(url), url
    try:
        db._run_lightweight_migrations()
        cols = {c["name"] for c in inspect(db.engine).get_columns("opportunities")}
        assert "next_action" in cols
    finally:
        db.engine.dispose()
        db.engine, db.DATABASE_URL = orig_engine, orig_url


def test_contact_activities_table_created_by_create_all():
    # La NOUVELLE table est créée par create_all (conditionnel, checkfirst) —
    # idempotent : deux appels ne cassent rien, et on peut y insérer/relire.
    e = create_engine("sqlite://")
    SQLModel.metadata.create_all(e)
    SQLModel.metadata.create_all(e)  # idempotence
    with Session(e) as s:
        opp = _opp(s)
        s.add(ContactActivity(opportunity_id=opp.id, type="dm_insta", note="DM"))
        s.commit()
        got = s.exec(select(ContactActivity)).all()
        assert len(got) == 1 and got[0].type == "dm_insta"
