"""run_places (B2, T4) — orchestration sans réseau (api_post injecté, checkpoint
temp, session mémoire). Vérifie : création des leads source='places', téléphone
recopié depuis raw sur Opportunity.phone, contact_confidence='moyenne' (décision
#9/#10), commit par candidat (isolation)."""
from datetime import date

from sqlmodel import Session, SQLModel, create_engine, select

from app.ingestion.places_sweep import CityCheckpoint
from app.ingestion.pipeline import run_places
from app.models import Opportunity


def _engine():
    e = create_engine("sqlite://")
    SQLModel.metadata.create_all(e)
    return e


def _place(pid, name, phone="01 02 03 04 05"):
    """Shape brut de l'API Google Places (New) — transformé par search_places_text."""
    return {"id": pid, "displayName": {"text": name},
            "formattedAddress": f"{name} 75001 Paris",
            "nationalPhoneNumber": phone, "websiteUri": f"https://{pid}.fr",
            "userRatingCount": 12, "primaryType": "interior_designer"}


def test_run_places_creates_leads_phone_and_moyenne(monkeypatch, tmp_path):
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "x")
    served = {"n": 0}

    def fake_post(url, headers, json):
        served["n"] += 1
        if served["n"] == 1:  # 1re requête ville -> 2 fiches archi-valides
            return {"places": [_place("aa", "Atelier Interieur Nord"),
                               _place("bb", "Studio Deco Sud")],
                    "nextPageToken": None}
        return {"places": [], "nextPageToken": None}  # 2e requête ville -> vide

    cp = CityCheckpoint(path=str(tmp_path / "cp.json"))
    with Session(_engine()) as s:
        stats = run_places(cities=1, budget_eur=10.0, max_pages=1,
                           session=s, api_post=fake_post, checkpoint=cp)
        rows = s.exec(select(Opportunity).where(Opportunity.source == "places")).all()
        assert len(rows) == 2
        assert stats.created == 2
        for o in rows:
            assert o.population == "architecte"
            assert o.phone == "01 02 03 04 05"        # recopié depuis raw['phone']
            assert o.contact_confidence == "moyenne"  # sémantique décision #9/#10


def test_run_places_merges_into_muted_insta_and_fills_phone(monkeypatch, tmp_path):
    """La fiche survivante Insta (muette, sans téléphone) est ENRICHIE par le
    téléphone Places (exigence 2), pas dupliquée. Exerce l'index de dédup
    préchargé du run + la corroboration par domaine."""
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "x")
    from app.ingestion.base import LeadCandidate
    from app.ingestion.pipeline import IngestStats, _process_candidate

    with Session(_engine()) as s:
        # Studio Insta existant SANS téléphone, avec un site (support de corroboration).
        _process_candidate(s, LeadCandidate(
            source="instagram", source_ref="studio_lumen",
            establishment_name="Studio Lumen", city="Paris", address="",
            website="https://studiolumen.fr",
            main_signal="prescripteur actif", detection_date=date(2026, 7, 11),
            establishment_type="architecte d'intérieur", population="architecte"),
            IngestStats(source="instagram"), set(), None)
        s.commit()

        def fake_post(url, headers, json):
            # Une fiche Places même studio : même nom+ville, MÊME domaine, téléphone.
            return {"places": [{
                "id": "gp1", "displayName": {"text": "Studio Lumen"},
                "formattedAddress": "3 rue de Paris 75001 Paris",
                "nationalPhoneNumber": "01 44 55 66 77",
                "websiteUri": "https://studiolumen.fr",
                "userRatingCount": 8, "primaryType": "interior_designer"}],
                "nextPageToken": None}

        cp = CityCheckpoint(path=str(tmp_path / "cp.json"))
        stats = run_places(cities=1, budget_eur=10.0, max_pages=1,
                           session=s, api_post=fake_post, checkpoint=cp)
        rows = s.exec(select(Opportunity).where(
            Opportunity.population == "architecte")).all()
        assert len(rows) == 1                          # fusion, pas de doublon
        assert rows[0].source == "instagram"           # la fiche Insta survit
        assert rows[0].phone == "01 44 55 66 77"       # téléphone Places comble le vide
        assert stats.updated == 1
        assert ("places:gp1", "studio_lumen") in stats.soft_merges
