"""Pagination backend + tri composite de l'endpoint liste (B, T5). Aucun réseau.

L'endpoint renvoyait `.all()` sans limite — intenable à 30k lignes. On borne
par `limit`/`offset`, on expose le total via l'en-tête `X-Total-Count`, et le
tri par défaut (score) départage à score égal les fiches contactables (téléphone
présent) avant les muettes, puis les plus récentes.
"""
from datetime import date

from fastapi import Response
from sqlmodel import Session, SQLModel, create_engine

from app.models import Opportunity
from app.routes.opportunities import list_opportunities


def _engine():
    e = create_engine("sqlite://")
    SQLModel.metadata.create_all(e)
    return e


def _opp(name, score, phone=None, detection="2026-07-01", pop="architecte"):
    return Opportunity(
        establishment_name=name, establishment_type="architecte d'intérieur",
        city="Paris", address="", main_signal="prescripteur actif",
        detection_date=date.fromisoformat(detection), estimated_timing="",
        opportunity_score=score, phone=phone, population=pop,
    )


def test_limit_offset_and_total_count_header():
    with Session(_engine()) as s:
        for i in range(3):
            s.add(_opp(f"S{i}", score=5 - i))
        s.commit()
        resp = Response()
        rows = list_opportunities(response=resp, session=s, limit=2, offset=0)
        assert len(rows) == 2
        assert resp.headers["X-Total-Count"] == "3"
        # page suivante
        rows2 = list_opportunities(response=resp, session=s, limit=2, offset=2)
        assert len(rows2) == 1


def test_composite_sort_phone_present_first_at_equal_score():
    with Session(_engine()) as s:
        s.add(_opp("Muet", score=4, phone=None))
        s.add(_opp("Contactable", score=4, phone="01 02 03 04 05"))
        s.commit()
        rows = list_opportunities(response=Response(), session=s)
        # à score égal, la fiche avec téléphone d'abord.
        assert [o.establishment_name for o in rows] == ["Contactable", "Muet"]


def test_composite_sort_score_desc_then_recent():
    with Session(_engine()) as s:
        s.add(_opp("Haut", score=8, phone="01"))
        s.add(_opp("BasAncien", score=2, phone="01", detection="2026-01-01"))
        s.add(_opp("BasRecent", score=2, phone="01", detection="2026-07-01"))
        s.commit()
        rows = list_opportunities(response=Response(), session=s)
        # score desc en tête, puis à score égal le plus récent avant l'ancien.
        assert [o.establishment_name for o in rows] == ["Haut", "BasRecent", "BasAncien"]


def test_endpoint_callable_without_response_object():
    # Non-régression : les appels directs historiques (tests A1) passent
    # `session=` sans `response=` -> l'en-tête est simplement omis, pas d'erreur.
    with Session(_engine()) as s:
        s.add(_opp("Solo", score=3))
        s.commit()
        rows = list_opportunities(session=s, population="architecte")
        assert [o.establishment_name for o in rows] == ["Solo"]
