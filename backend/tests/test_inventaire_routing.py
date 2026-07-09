# backend/tests/test_inventaire_routing.py
"""Routage des labels en leads (brique 3bis, T2) — sans réseau ni LLM réels."""
import json
from datetime import date
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine, select

import app.ingestion.instagram as ig
import app.ingestion.pipeline as pl
from app.models import HandleVerdict, Opportunity

SNAP = Path(__file__).resolve().parents[1] / "app" / "ingestion" / "eval" / "snapshots"


class _FakeCompletion:
    def __init__(self, content):
        self.choices = [type("C", (), {"message": type("M", (), {"content": content})()})()]


class _FakeClient:
    def __init__(self, content):
        class _Completions:
            def create(_self, **kwargs):
                return _FakeCompletion(content)
        self.chat = type("Chat", (), {"completions": _Completions()})()


def _no_enricher():
    class _NoEnricher:
        def enrich(self, cand):
            return None

        def lookup(self, siren):
            return None
    return _NoEnricher()


def _prep(monkeypatch, profiles, judge_json=None):
    monkeypatch.setattr(pl, "scrape_profiles", lambda handles, **k: profiles)
    monkeypatch.setattr(pl, "match_siret", lambda **kw: None)
    monkeypatch.setattr(pl, "SireneEnricher", lambda: _no_enricher())
    if judge_json is None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    else:
        monkeypatch.setenv("OPENAI_API_KEY", "x")
        monkeypatch.setattr(ig, "_openai_client", lambda: _FakeClient(judge_json))


def _post(handle, caption="restaurant, ouverture prochaine à Paris",
          hashtags=("ouvertureprochaine",)):
    # NB fixture : la caption par défaut porte un mot-clé CHR ("restaurant") pour
    # franchir le filtre CHR de discover() (_is_chr) — sinon un handle générique
    # (douteux/loumas/neuf) serait écarté AVANT le routage testé ici. N'affecte
    # aucune assertion de routage (le label vient du profil/juge, pas de la caption).
    return {"ownerUsername": handle, "ownerFullName": handle, "caption": caption,
            "hashtags": list(hashtags), "locationName": "Paris"}


def _run(engine, posts):
    with Session(engine) as s:
        stats = pl.run_instagram(posts=posts, session=s)
        s.commit()
        opps = {o.source_ref: o for o in s.exec(select(Opportunity)).all()}
        verdicts = {v.handle: v.verdict for v in s.exec(select(HandleVerdict)).all()}
        return stats, opps, verdicts


def _engine(tmp_path):
    e = create_engine(f"sqlite:///{tmp_path/'t.db'}")
    SQLModel.metadata.create_all(e)
    return e


def test_established_becomes_low_score_lead(tmp_path, monkeypatch):
    # postsCount > 150 -> garde-fou established (pas de LLM).
    prof = {"postsCount": 200, "biography": "Bistrot de quartier",
            "latestPosts": [{"timestamp": "2026-07-01T10:00:00.000Z"}]}
    _prep(monkeypatch, {"vieuxbistrot": prof})
    _, opps, verdicts = _run(_engine(tmp_path), [_post("vieuxbistrot", "resto à Paris", ())])
    opp = opps["vieuxbistrot"]
    assert opp.lifecycle_label == "established"
    assert opp.main_signal == "établissement en activité"
    assert not (set(opp.secondary_signals or []))  # aucun signal secondaire
    assert opp.opportunity_score <= 5               # naturellement bas (aucun bonus d'ouverture)
    assert verdicts["vieuxbistrot"] == "established"  # caché comme avant


def test_chain_lead_has_multisite_secondary(tmp_path, monkeypatch):
    moka = json.loads((SNAP / "cafe_mokaparis.json").read_text(encoding="utf-8"))
    _prep(monkeypatch, {"cafe_mokaparis": moka})
    _, opps, verdicts = _run(_engine(tmp_path), [_post("cafe_mokaparis", "café à Paris", ("cafeparis",))])
    opp = opps["cafe_mokaparis"]
    assert opp.lifecycle_label == "chain_multisite"
    assert "extension multi-sites" in (opp.secondary_signals or [])
    assert opp.main_signal == "établissement en activité"
    assert verdicts["cafe_mokaparis"] == "chain_multisite"


def test_unknown_lead_is_neutral_not_disguised(tmp_path, monkeypatch):
    prof = {"postsCount": 2, "biography": "Ouverture prochaine",
            "latestPosts": [{"timestamp": "2026-06-20T10:00:00.000Z"}]}
    _prep(monkeypatch, {"douteux": prof})  # pas de juge -> unknown
    _, opps, _ = _run(_engine(tmp_path), [_post("douteux")])
    opp = opps["douteux"]
    assert opp.lifecycle_label == "unknown"
    # Plus de faux « ouverture prochaine » : signal neutre.
    assert opp.main_signal == "établissement en activité"


def test_not_venue_and_noise_no_lead_but_cached(tmp_path, monkeypatch):
    prof = {"postsCount": 3, "biography": "Marque de bijoux",
            "latestPosts": [{"timestamp": "2026-06-20T10:00:00.000Z", "caption": "collection"}]}
    for label in ("not_venue", "noise"):
        # Handle distinct par label : le cache de verdicts persiste dans la même
        # base tmp_path d'une itération à l'autre ; réutiliser "marque" ferait
        # sauter la 2e passe (should_rejudge) et figerait le 1er verdict.
        handle = "marque" + label.replace("_", "")
        _prep(monkeypatch, {handle: prof},
              judge_json=('{"reasoning":"x","label":"%s","confidence":"haute",'
                          '"addresses":[],"emails":[],"opening_date":null}' % label))
        _, opps, verdicts = _run(_engine(tmp_path),
                                 [_post(handle, "restaurant bijoux à Paris", ())])
        assert handle not in opps                  # pas de lead
        assert verdicts.get(handle) == label       # mais verdict caché


def test_opening_still_hot(tmp_path, monkeypatch):
    prof = {"postsCount": 2, "biography": "on ouvre bientôt",
            "latestPosts": [{"timestamp": "2026-06-20T10:00:00.000Z", "caption": "travaux"}]}
    _prep(monkeypatch, {"loumas": prof},
          judge_json='{"reasoning":"x","label":"opening_soon","confidence":"haute",'
                     '"addresses":[],"emails":[],"opening_date":null}')
    _, opps, _ = _run(_engine(tmp_path), [_post("loumas")])
    opp = opps["loumas"]
    assert opp.lifecycle_label == "opening_soon"
    assert opp.main_signal == "ouverture prochaine"


def test_established_lead_stage_is_coherent(tmp_path, monkeypatch):
    # Cohérence bout-en-bout : une fiche « en base » established (signal NEUTRE, ni
    # avis, ni origine, ni date d'activité) doit exposer lifecycle_stage='établi',
    # jamais 'ouvert récemment' (sinon contradiction avec lifecycle_label).
    from app.schemas import OpportunityList
    prof = {"postsCount": 200, "biography": "Bistrot de quartier",
            "latestPosts": [{"timestamp": "2026-07-01T10:00:00.000Z"}]}
    _prep(monkeypatch, {"vieuxbistrot": prof})
    _, opps, _ = _run(_engine(tmp_path), [_post("vieuxbistrot", "resto à Paris", ())])
    opp = opps["vieuxbistrot"]
    assert opp.lifecycle_label == "established"
    serialized = OpportunityList.model_validate(opp)
    assert serialized.lifecycle_stage == "établi"


def test_verdict_not_cached_when_lead_creation_fails(tmp_path, monkeypatch):
    # Fix revue finale : le verdict est committé AVEC le lead (même transaction).
    # Si _process_candidate échoue, le rollback annule AUSSI le verdict -> le handle
    # reste « dû » (should_rejudge True) et sera re-jugé, au lieu d'être endormi 6
    # mois sans jamais avoir créé sa fiche « en base ».
    from app.ingestion import verdict_cache
    prof = {"postsCount": 200, "biography": "Bistrot de quartier",
            "latestPosts": [{"timestamp": "2026-07-01T10:00:00.000Z"}]}
    _prep(monkeypatch, {"vieuxbistrot": prof})

    def _boom(*a, **k):
        raise RuntimeError("échec enrichissement simulé")
    monkeypatch.setattr(pl, "_process_candidate", _boom)

    engine = _engine(tmp_path)
    with Session(engine) as s:
        stats = pl.run_instagram(posts=[_post("vieuxbistrot", "resto à Paris", ())], session=s)
        s.commit()
        assert stats.errors == 1
        # Ni lead, ni verdict caché : le handle doit rester re-jugé.
        assert s.exec(select(Opportunity)).all() == []
        assert s.exec(select(HandleVerdict)).all() == []
        assert verdict_cache.should_rejudge(s, "vieuxbistrot") is True


def test_opening_outranks_established(tmp_path, monkeypatch):
    profs = {
        "vieux": {"postsCount": 200, "biography": "Bistrot",
                  "latestPosts": [{"timestamp": "2026-07-01T10:00:00.000Z"}]},
        "neuf": {"postsCount": 2, "biography": "on ouvre bientôt",
                 "latestPosts": [{"timestamp": "2026-06-20T10:00:00.000Z", "caption": "travaux"}]},
    }
    _prep(monkeypatch, profs,
          judge_json='{"reasoning":"x","label":"opening_soon","confidence":"haute",'
                     '"addresses":[],"emails":[],"opening_date":null}')
    _, opps, _ = _run(_engine(tmp_path),
                      [_post("neuf"), _post("vieux", "resto à Paris", ())])
    assert opps["neuf"].opportunity_score > opps["vieux"].opportunity_score
