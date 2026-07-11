"""Échantillonneur GT stock (B, T6) — tire N leads `sirene_stock` qualifiés au
hasard de la base pour annotation manuelle + calcul de précision depuis le CSV
annoté (gate paramétrable). Aucun réseau, DB mémoire, RNG graine fixe."""
import csv
import random
from datetime import date
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine

from app.ingestion.eval.stock_gt_sample import (
    SAMPLE_HEADER, load_annotated, sample_stock_leads, stock_precision,
    write_sample_csv,
)
from app.models import Opportunity


def _engine():
    e = create_engine("sqlite://")
    SQLModel.metadata.create_all(e)
    return e


def _opp(source, name, ref, siren=None, city="Lyon"):
    return Opportunity(
        source=source, source_ref=ref, establishment_name=name, city=city,
        address="", main_signal="prescripteur actif", population="architecte",
        siren=siren, detection_date=date(2026, 7, 11), estimated_timing="",
        establishment_type="architecte d'intérieur",
        secondary_signals=["stock sirene"])


def test_sample_draws_only_stock_leads_with_empty_label():
    with Session(_engine()) as s:
        stock_refs = {f"{i:014d}" for i in range(10)}
        for i in range(10):
            s.add(_opp("sirene_stock", f"Studio {i}", f"{i:014d}", siren=f"{i:09d}"))
        s.add(_opp("instagram", "Insta X", "insta_x"))    # autre source -> exclu
        s.add(_opp("places", "Place Y", "places:y"))       # autre source -> exclu
        s.commit()
        rows = sample_stock_leads(s, 5, rng=random.Random(0))
        assert len(rows) == 5
        assert all(set(r) >= {"handle", "denomination", "siren", "ville", "label"}
                   for r in rows)
        assert all(r["label"] == "" for r in rows)          # colonne à annoter
        assert all(r["handle"] in stock_refs for r in rows)  # uniquement le stock


def test_sample_caps_at_available_and_is_random_with_seed():
    with Session(_engine()) as s:
        for i in range(3):
            s.add(_opp("sirene_stock", f"Atelier {i}", f"{i:014d}", siren=f"{i:09d}"))
        s.commit()
        rows = sample_stock_leads(s, 100, rng=random.Random(1))  # borne à 3 dispo
        assert len(rows) == 3
        # reproductible à graine égale
        a = [r["handle"] for r in sample_stock_leads(s, 3, rng=random.Random(7))]
        b = [r["handle"] for r in sample_stock_leads(s, 3, rng=random.Random(7))]
        assert a == b


def test_csv_roundtrip(tmp_path):
    rows = [{"handle": "123", "denomination": "Studio Déco", "siren": "111",
             "ville": "Lyon", "label": ""}]
    p = tmp_path / "stock_gt.csv"
    write_sample_csv(rows, str(p))
    back = load_annotated(str(p))
    assert list(csv.reader(p.open(encoding="utf-8")))[0] == list(SAMPLE_HEADER)
    assert back[0]["denomination"] == "Studio Déco" and back[0]["label"] == ""


def test_stock_precision_ignores_unannotated_and_is_case_insensitive():
    rows = [{"label": "cible"}, {"label": "CIBLE"}, {"label": "cible"},
            {"label": "hors_cible"}, {"label": ""}, {"label": "  "}]
    prec, tp, n = stock_precision(rows)
    assert (tp, n) == (3, 4)               # 4 annotés, 3 cible
    assert abs(prec - 0.75) < 1e-9


def test_stock_precision_none_when_no_annotation():
    prec, tp, n = stock_precision([{"label": ""}, {"label": ""}])
    assert prec is None and (tp, n) == (0, 0)


def test_stock_precision_custom_positive_labels():
    rows = [{"label": "studio_actif"}, {"label": "hors_cible"}]
    prec, tp, n = stock_precision(rows, positive={"studio_actif"},
                                  negative={"hors_cible"})
    assert (tp, n) == (1, 2) and abs(prec - 0.5) < 1e-9
