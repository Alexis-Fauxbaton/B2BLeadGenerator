# backend/tests/test_funnel_v2.py
"""Tests du funnel Insta v2 recâblé (brique 3) — sans réseau ni LLM réels."""
import json
from datetime import date
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine, select

import app.ingestion.pipeline as pl
from app.ingestion.instagram import classify_profiles
from app.ingestion.enrichment.siret_matcher import MatchResult
from app.models import HandleVerdict, Opportunity

SNAP = Path(__file__).resolve().parents[1] / "app" / "ingestion" / "eval" / "snapshots"
TODAY = date(2026, 7, 6)


class _FakeCompletion:
    def __init__(self, content):
        self.choices = [type("C", (), {"message": type("M", (), {"content": content})()})()]


class _FakeClient:
    def __init__(self, content):
        self._content = content

        class _Completions:
            def create(_self, **kwargs):
                return _FakeCompletion(content)

        self.chat = type("Chat", (), {"completions": _Completions()})()


def test_classify_guard_kills_moka_without_llm():
    snap = json.loads((SNAP / "cafe_mokaparis.json").read_text(encoding="utf-8"))
    cands = [{"handle": "cafe_mokaparis", "name": "MOKA", "city": "Paris", "type": "café"}]
    out = classify_profiles(cands, {"cafe_mokaparis": snap}, client=None,
                            match_fn=None, today=TODAY)
    assert out[0]["label"] == "chain_multisite"


def test_classify_unknown_when_no_client():
    prof = {"postsCount": 2, "biography": "Ouverture prochaine",
            "latestPosts": [{"timestamp": "2026-06-20T10:00:00.000Z"}]}
    cands = [{"handle": "newresto", "name": "Le Nouveau", "city": "Paris", "type": "restaurant"}]
    out = classify_profiles(cands, {"newresto": prof}, client=None, match_fn=None, today=TODAY)
    # Pas de juge -> doute -> unknown (gardé, protège le recall).
    assert out[0]["label"] == "unknown"


def test_classify_runs_matcher_before_judge():
    prof = {"postsCount": 2, "biography": "on ouvre",
            "latestPosts": [{"timestamp": "2026-06-20T10:00:00.000Z", "caption": "travaux"}]}
    seen = {}

    def fake_match(lead):
        seen["called"] = True
        return MatchResult(siren="1", siret="1", naf="56.10A", enseigne="OCOIN",
                           confidence="moyenne", method="arbitre", date_creation="2026-06-01")

    client = _FakeClient('{"reasoning": "x", "label": "opening_soon", '
                         '"confidence": "haute", "addresses": [], "emails": [], '
                         '"opening_date": null}')
    cands = [{"handle": "x", "name": "X", "city": "Paris", "type": "restaurant"}]
    out = classify_profiles(cands, {"x": prof}, client=client, match_fn=fake_match, today=TODAY)
    assert seen.get("called") is True
    assert out[0]["label"] == "opening_soon"
    assert out[0]["_match"].siren == "1"


def _no_enricher():
    class _NoEnricher:
        def enrich(self, cand):
            return None

        def lookup(self, siren):
            return None
    return _NoEnricher()


def test_run_instagram_labels_leads_and_cache(tmp_path, monkeypatch):
    """MOKA -> chain_multisite (verdict caché, pas de lead) ; newresto ->
    unknown (lead créé). Verdicts écrits en cache pour les deux."""
    engine = create_engine(f"sqlite:///{tmp_path/'t.db'}")
    SQLModel.metadata.create_all(engine)
    moka = json.loads((SNAP / "cafe_mokaparis.json").read_text(encoding="utf-8"))
    profiles = {
        "cafe_mokaparis": moka,
        "newresto": {"postsCount": 2, "biography": "Ouverture prochaine",
                     "latestPosts": [{"timestamp": "2026-06-20T10:00:00.000Z"}]},
    }
    monkeypatch.setattr(pl, "scrape_profiles", lambda handles, **k: profiles)
    monkeypatch.setattr(pl, "match_siret", lambda **kw: None)  # pas de réseau matcher
    monkeypatch.setattr(pl, "SireneEnricher", lambda: _no_enricher())
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)  # pas de juge -> unknown

    posts = [
        {"ownerUsername": "newresto", "ownerFullName": "Le Nouveau Resto",
         "caption": "Ouverture prochaine à Paris", "hashtags": ["ouvertureprochaine"],
         "locationName": "Paris, France"},
        {"ownerUsername": "cafe_mokaparis", "ownerFullName": "MOKA",
         "caption": "café à Paris", "hashtags": ["cafeparis"], "locationName": "Paris"},
    ]
    with Session(engine) as s:
        stats = pl.run_instagram(posts=posts, session=s)
        s.commit()
        handles = {o.source_ref for o in s.exec(select(Opportunity)).all()}
        assert "newresto" in handles           # unknown -> lead
        assert "cafe_mokaparis" not in handles  # chain_multisite -> pas de lead
        verdicts = {v.handle: v.verdict for v in s.exec(select(HandleVerdict)).all()}
        assert verdicts["cafe_mokaparis"] == "chain_multisite"
        assert verdicts["newresto"] == "unknown"
    assert stats.errors == 0


def test_run_instagram_skips_cached_handle(tmp_path, monkeypatch):
    """Un handle déjà jugé not_venue dans la fenêtre n'est PAS re-scrapé."""
    from app.ingestion import verdict_cache as vc
    engine = create_engine(f"sqlite:///{tmp_path/'t.db'}")
    SQLModel.metadata.create_all(engine)
    scraped = {"handles": None}

    def fake_scrape(handles, **k):
        scraped["handles"] = list(handles)
        return {}

    monkeypatch.setattr(pl, "scrape_profiles", fake_scrape)
    monkeypatch.setattr(pl, "match_siret", lambda **kw: None)
    monkeypatch.setattr(pl, "SireneEnricher", lambda: _no_enricher())
    posts = [{"ownerUsername": "dejavu", "ownerFullName": "Resto Déjà Vu",
              "caption": "restaurant à Paris", "hashtags": [], "locationName": "Paris"}]
    with Session(engine) as s:
        vc.upsert(s, "dejavu", "not_venue", "haute",
                  {"biography": "x", "postsCount": 1})
        s.commit()
        pl.run_instagram(posts=posts, session=s)
        # 'dejavu' est dans la fenêtre 12 mois -> aucun handle dû -> scrape jamais
        # appelé (run_instagram court-circuite le scrape quand `due` est vide).
        assert scraped["handles"] is None
