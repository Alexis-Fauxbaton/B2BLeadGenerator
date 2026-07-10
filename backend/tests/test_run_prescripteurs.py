"""run_prescripteurs recâblé (A1, T4) — sans réseau ni LLM réels."""
from datetime import date

from sqlmodel import Session, SQLModel, create_engine, select

import app.ingestion.instagram as ig
import app.ingestion.pipeline as pl
from app.ingestion.instagram import extract_tagged_studios
from app.models import HandleVerdict, Opportunity


class _FakeCompletion:
    def __init__(self, content):
        self.choices = [type("C", (), {"message": type("M", (), {"content": content})()})()]


class _FakeClient:
    def __init__(self, content):
        class _Completions:
            def create(_self, **kwargs):
                return _FakeCompletion(content)
        self.chat = type("Chat", (), {"completions": _Completions()})()


def _engine(tmp_path):
    e = create_engine(f"sqlite:///{tmp_path/'t.db'}")
    SQLModel.metadata.create_all(e)
    return e


def _post(handle, caption="Projet d'archi", hashtags=("architectedinterieur",)):
    return {"ownerUsername": handle, "ownerFullName": handle, "caption": caption,
            "hashtags": list(hashtags), "locationName": "Paris"}


def _prep(monkeypatch, profiles, judge_json=None, tagged=None):
    monkeypatch.setattr(pl, "scrape_profiles", lambda handles, **k: profiles)
    monkeypatch.setattr(pl, "match_siret", lambda **kw: None)
    # Pas de tagged auto : injecté explicitement (évite un 2e scrape en test).
    if judge_json is None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    else:
        monkeypatch.setenv("OPENAI_API_KEY", "x")
        monkeypatch.setattr(ig, "_openai_client", lambda: _FakeClient(judge_json))


def test_extract_tagged_studios_pure():
    profiles = {
        "resto1": {"latestPosts": [
            {"caption": "Merci @atelierdularge pour le design ! @non_studio"},
            {"caption": "Ambiance signée @bifur.architecture"}]},
    }
    tags = extract_tagged_studios(profiles)
    assert "atelierdularge" in tags and "bifur.architecture" in tags
    assert "non_studio" in tags  # extraction brute ; le filtrage se fait au match handle


def test_hors_cible_no_lead_but_cached(tmp_path, monkeypatch):
    prof = {"biography": "Menuiserie & Ébénisterie", "fullName": "Menuiserie",
            "postsCount": 72, "followersCount": 335,
            "latestPosts": [{"timestamp": "2026-07-01T10:00:00.000Z"}]}
    _prep(monkeypatch, {"menuis": prof})
    with Session(_engine(tmp_path)) as s:
        pl.run_prescripteurs(posts=[_post("menuis", "menuiserie", ("agencement",))],
                             session=s, tagged_studios=set())
        s.commit()
        assert s.exec(select(Opportunity)).all() == []  # pas de lead
        verdicts = {v.handle: v.verdict for v in s.exec(select(HandleVerdict)).all()}
        assert verdicts.get("arch:menuis") == "hors_cible"     # mais verdict caché (clé préfixée)


def test_studio_actif_becomes_architect_lead(tmp_path, monkeypatch):
    prof = {"biography": "Architecte d'intérieur à Paris", "postsCount": 40, "followersCount": 500,
            "latestPosts": [{"timestamp": "2026-07-05T10:00:00.000Z", "caption": "Projet"}]}
    _prep(monkeypatch, {"studioa": prof},
          judge_json='{"reasoning":"x","label":"studio_actif","confidence":"haute",'
                     '"hospitality_proof":false,"addresses":[],"emails":[]}')
    with Session(_engine(tmp_path)) as s:
        pl.run_prescripteurs(posts=[_post("studioa")], session=s, tagged_studios=set())
        s.commit()
        opp = s.exec(select(Opportunity).where(Opportunity.source_ref == "studioa")).first()
        assert opp is not None
        assert opp.population == "architecte"
        assert opp.establishment_type == "architecte d'intérieur"
        assert opp.main_signal == "prescripteur actif"
        assert opp.lifecycle_label == "studio_actif"


def test_t1_tagged_studio_gets_hot_secondary(tmp_path, monkeypatch):
    prof = {"biography": "Architecte d'intérieur", "postsCount": 40, "followersCount": 500,
            "latestPosts": [{"timestamp": "2026-07-05T10:00:00.000Z", "caption": "Projet"}]}
    _prep(monkeypatch, {"atelierdularge": prof},
          judge_json='{"reasoning":"x","label":"studio_actif","confidence":"haute",'
                     '"hospitality_proof":false,"addresses":[],"emails":[]}')
    with Session(_engine(tmp_path)) as s:
        pl.run_prescripteurs(posts=[_post("atelierdularge")], session=s,
                             tagged_studios={"atelierdularge"})
        s.commit()
        opp = s.exec(select(Opportunity).where(Opportunity.source_ref == "atelierdularge")).first()
        assert "projet CHR détecté" in (opp.secondary_signals or [])
        # T1 doit scorer plus haut qu'un T3 générique.
        assert opp.opportunity_score >= 5


def test_fail_soft_studio_actif_basse_not_cached(tmp_path, monkeypatch):
    prof = {"biography": "Architecte d'intérieur", "postsCount": 40, "followersCount": 500,
            "latestPosts": [{"timestamp": "2026-07-05T10:00:00.000Z", "caption": "Projet"}]}
    _prep(monkeypatch, {"douteux": prof})  # pas de juge -> studio_actif basse
    with Session(_engine(tmp_path)) as s:
        pl.run_prescripteurs(posts=[_post("douteux")], session=s, tagged_studios=set())
        s.commit()
        opp = s.exec(select(Opportunity).where(Opportunity.source_ref == "douteux")).first()
        assert opp is not None and opp.lifecycle_label == "studio_actif"
        verdicts = {v.handle for v in s.exec(select(HandleVerdict)).all()}
        assert "douteux" not in verdicts  # basse fail-soft : non caché (re-jugé au prochain run)


def _seed_architecte_lead(session, handle):
    """Crée une fiche archi 'studio_actif' déjà en base (simule un run précédent)."""
    from app.ingestion.base import LeadCandidate
    from app.ingestion.pipeline import IngestStats, _process_candidate
    _process_candidate(session, LeadCandidate(
        source="instagram", source_ref=handle, establishment_name=handle,
        city="Paris", address="", main_signal="prescripteur actif",
        detection_date=date(2026, 6, 1), population="architecte",
        establishment_type="architecte d'intérieur", instagram=handle,
        lifecycle_label="studio_actif"), IngestStats(source="instagram"), set(), None)
    session.commit()


def test_requalif_hors_cible_purges_existing_architecte_lead(tmp_path, monkeypatch):
    # Fiche archi existante + re-verdict hors_cible (garde design-build : bio magasin
    # d'ameublement) -> la fiche caduque est SUPPRIMÉE et stats.purged=1.
    from app.models import ContactHistory, Signal
    prof = {"biography": "Designer & architecte d'intérieur\nMagasin d'ameublement et décoration",
            "fullName": "Bontemps", "postsCount": 198, "followersCount": 470,
            "latestPosts": [{"timestamp": "2026-07-05T10:00:00.000Z", "caption": "cuisine sur mesure"}]}
    _prep(monkeypatch, {"rolitech": prof})
    with Session(_engine(tmp_path)) as s:
        _seed_architecte_lead(s, "rolitech")
        assert s.exec(select(Opportunity).where(Opportunity.source_ref == "rolitech")).first() is not None
        stats = pl.run_prescripteurs(posts=[_post("rolitech", "magasin", ("agencement",))],
                                     session=s, tagged_studios=set())
        s.commit()
        assert stats.purged == 1
        assert s.exec(select(Opportunity).where(Opportunity.source_ref == "rolitech")).first() is None
        # Purge propre : ni Signal ni ContactHistory orphelins.
        assert s.exec(select(Signal)).all() == []
        assert s.exec(select(ContactHistory)).all() == []


def test_requalif_studio_actif_keeps_and_updates_existing_lead(tmp_path, monkeypatch):
    # Fiche archi existante + re-verdict studio_actif -> CONSERVÉE (mise à jour),
    # aucune purge.
    prof = {"biography": "Architecte d'intérieur à Paris", "postsCount": 60, "followersCount": 800,
            "latestPosts": [{"timestamp": "2026-07-05T10:00:00.000Z", "caption": "Projet"}]}
    _prep(monkeypatch, {"studioa": prof},
          judge_json='{"reasoning":"x","label":"studio_actif","confidence":"haute",'
                     '"hospitality_proof":false,"addresses":[],"emails":[]}')
    with Session(_engine(tmp_path)) as s:
        _seed_architecte_lead(s, "studioa")
        stats = pl.run_prescripteurs(posts=[_post("studioa")], session=s, tagged_studios=set())
        s.commit()
        assert stats.purged == 0 and stats.updated == 1
        opp = s.exec(select(Opportunity).where(Opportunity.source_ref == "studioa")).first()
        assert opp is not None and opp.lifecycle_label == "studio_actif"


def test_requalif_not_venue_purges_existing_chr_lead(tmp_path, monkeypatch):
    # SYMÉTRIE CHR : une fiche CHR existante re-jugée not_venue (funnel run_instagram)
    # est purgée elle aussi (même trou, même correctif).
    from app.ingestion.base import LeadCandidate
    from app.ingestion.pipeline import IngestStats, _process_candidate
    monkeypatch.setattr(pl, "scrape_profiles", lambda handles, **k: {"exresto": {
        "biography": "compte perso", "postsCount": 5, "followersCount": 20,
        "latestPosts": [{"timestamp": "2026-07-05T10:00:00.000Z"}]}})
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    # Force un verdict not_venue déterministe via classify_profiles injecté.
    monkeypatch.setattr(pl, "classify_profiles",
                        lambda due, profiles, **k: [dict(c, label="not_venue", confidence="haute") for c in due])
    with Session(_engine(tmp_path)) as s:
        _process_candidate(s, LeadCandidate(
            source="instagram", source_ref="exresto", establishment_name="Ex Resto",
            city="Paris", address="", main_signal="ouverture prochaine",
            detection_date=date(2026, 6, 1), establishment_type="restaurant",
            instagram="exresto", lifecycle_label="opening_soon"),
            IngestStats(source="instagram"), set(), None)
        s.commit()
        assert s.exec(select(Opportunity).where(Opportunity.source_ref == "exresto")).first() is not None
        stats = pl.run_instagram(posts=[_post("exresto", "resto", ("restaurant",))], session=s)
        s.commit()
        assert stats.purged == 1
        assert s.exec(select(Opportunity).where(Opportunity.source_ref == "exresto")).first() is None


def test_build_tagged_studios_from_chr_leads(tmp_path, monkeypatch):
    from app.ingestion.base import LeadCandidate
    from app.ingestion.pipeline import IngestStats, _process_candidate, _build_tagged_studios
    with Session(_engine(tmp_path)) as s:
        # Un lead CHR Instagram existant.
        _process_candidate(s, LeadCandidate(source="instagram", source_ref="resto1",
                           establishment_name="Resto", city="Paris", address="",
                           main_signal="ouverture prochaine", detection_date=date(2026, 7, 1),
                           establishment_type="restaurant", instagram="resto1"),
                           IngestStats(source="instagram"), set(), enricher=None)
        s.commit()
        fake_scrape = lambda handles, **k: {"resto1": {"latestPosts": [
            {"caption": "design @atelierdularge"}]}}
        tags = _build_tagged_studios(s, scrape_fn=fake_scrape)
        assert "atelierdularge" in tags
