"""Tests du script one-shot `app.ingestion.eval.backfill_cfai_societe`.
Base sqlite EN MÉMOIRE (session injectée) : la base réelle n'est jamais
touchée, aucun réseau."""
from datetime import date

from sqlmodel import Session, SQLModel, create_engine, select

from app.ingestion.eval.backfill_cfai_societe import backfill
from app.models import Opportunity


def _engine():
    e = create_engine("sqlite://")
    SQLModel.metadata.create_all(e)
    return e


def _opp(**kw):
    base = dict(
        establishment_name="X", establishment_type="architecte d'intérieur",
        city="Paris", address="", main_signal="prescripteur actif",
        detection_date=date(2026, 7, 12), estimated_timing="J-90",
        source="annuaire", population="architecte",
    )
    base.update(kw)
    return Opportunity(**base)


def test_backfill_replaces_placeholder_with_decision_maker():
    with Session(_engine()) as s:
        s.add(_opp(source_ref="cfai:1419", establishment_name="Exercice en libéral",
                   decision_maker="Laetitia DUVAL-BOQUET"))
        s.add(_opp(source_ref="cfai:566", establishment_name="En libéral depuis 2006",
                   decision_maker="Sophie PROUST"))
        # Vrai nom d'enseigne : intouché.
        s.add(_opp(source_ref="cfai:85", establishment_name="BUSH & Associates SAS",
                   decision_maker="Derek BUSH"))
        s.commit()
        fixed = backfill(s)
        assert sorted((avant, apres) for _, avant, apres in fixed) == [
            ("En libéral depuis 2006", "Sophie PROUST"),
            ("Exercice en libéral", "Laetitia DUVAL-BOQUET"),
        ]
        names = {o.source_ref: o.establishment_name
                 for o in s.exec(select(Opportunity)).all()}
        assert names["cfai:1419"] == "Laetitia DUVAL-BOQUET"
        assert names["cfai:566"] == "Sophie PROUST"
        assert names["cfai:85"] == "BUSH & Associates SAS"


def test_backfill_skips_non_cfai_and_missing_decision_maker():
    with Session(_engine()) as s:
        # Placeholder mais hors CFAI : la sémantique « saisie libre » ne
        # s'applique qu'au champ société CFAI -> intouché.
        s.add(_opp(source_ref="ufdi:9", establishment_name="En libéral depuis 2006",
                   decision_maker="Quelqu'un"))
        # CFAI placeholder mais décideur vide : VIDE > FAUX, pas de nom sûr
        # pour remplacer -> intouché.
        s.add(_opp(source_ref="cfai:56", establishment_name="MICRO ENTREPRISE",
                   decision_maker=None))
        s.commit()
        assert backfill(s) == []
        names = {o.source_ref: o.establishment_name
                 for o in s.exec(select(Opportunity)).all()}
        assert names["ufdi:9"] == "En libéral depuis 2006"
        assert names["cfai:56"] == "MICRO ENTREPRISE"
