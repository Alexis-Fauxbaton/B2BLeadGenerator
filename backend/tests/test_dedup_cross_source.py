"""Dédup cross-source généralisée (B, T4) — corroboration téléphone/domaine et
corroboration FORTE inter-masse (sirene_stock<->places). Aucun réseau : leads
injectés via _process_candidate (chemin de dédup partagé, comme test_run_annuaires).

Invariants vérifiés :
- un lead `places` corroboré (domaine/téléphone) FUSIONNE dans une fiche Insta
  muette et la comble (exigence 2) ;
- un homonyme `places` SANS corroboration ne fusionne pas (2 fiches) ;
- FIXTURE ADVERSE inter-masse : `sirene_stock`<->`places`, mêmes tokens de nom +
  même ville + MÊME numéro de voie mais téléphones/domaines DIFFÉRENTS -> PAS de
  merge (le géo seul ne suffit pas entre deux sources de masse, décision #11) ;
- asymétrie : un lead ENTRANT hors SOFT_DEDUP_SOURCES (instagram) ne déclenche
  jamais la fusion douce (A1/CHR bit-à-bit intacts)."""
from datetime import date

from sqlmodel import Session, SQLModel, create_engine, select

from app.ingestion.base import LeadCandidate
from app.ingestion.pipeline import (
    SOFT_DEDUP_SOURCES, IngestStats, _process_candidate,
)
from app.models import Opportunity


def _engine():
    e = create_engine("sqlite://")
    SQLModel.metadata.create_all(e)
    return e


def _seed(s, cand):
    _process_candidate(s, cand, IngestStats(source=cand.source), set(), None)
    s.commit()


def test_soft_dedup_sources_contains_stock_and_places():
    assert {"annuaire", "sirene_stock", "places"} <= SOFT_DEDUP_SOURCES


def test_places_merges_into_muted_insta_by_domain_and_fills_phone():
    with Session(_engine()) as s:
        _seed(s, LeadCandidate(
            source="instagram", source_ref="atelier_nord_insta",
            establishment_name="Atelier Nord", city="Lyon", address="",
            website="https://ateliernord.fr",
            main_signal="prescripteur actif", detection_date=date(2026, 7, 11),
            establishment_type="architecte d'intérieur", population="architecte"))
        stats = IngestStats(source="places")
        _process_candidate(s, LeadCandidate(
            source="places", source_ref="places:gp9",
            establishment_name="Atelier Nord", city="Lyon",
            address="5 rue Centrale 69001 Lyon",
            website="https://ateliernord.fr",   # même domaine -> corroboration
            main_signal="prescripteur actif", detection_date=date(2026, 7, 11),
            establishment_type="architecte d'intérieur", population="architecte",
            raw={"phone": "04 11 22 33 44"}), stats, set(), None)
        s.commit()
        rows = s.exec(select(Opportunity).where(
            Opportunity.population == "architecte")).all()
        assert len(rows) == 1 and rows[0].source == "instagram"
        assert rows[0].phone == "04 11 22 33 44"     # Places comble l'Insta muet
        assert stats.updated == 1
        assert ("places:gp9", "atelier_nord_insta") in stats.soft_merges


def test_places_homonym_without_corroboration_not_merged():
    with Session(_engine()) as s:
        _seed(s, LeadCandidate(
            source="instagram", source_ref="studio_x_insta",
            establishment_name="Studio Horizon", city="Paris", address="",
            website="https://studio-horizon.fr",
            main_signal="prescripteur actif", detection_date=date(2026, 7, 11),
            establishment_type="architecte d'intérieur", population="architecte"))
        stats = IngestStats(source="places")
        _process_candidate(s, LeadCandidate(
            source="places", source_ref="places:gp2",
            establishment_name="Studio Horizon", city="Paris", address="",
            website="https://un-autre-horizon.fr",   # domaine différent, pas de tél
            main_signal="prescripteur actif", detection_date=date(2026, 7, 11),
            establishment_type="architecte d'intérieur", population="architecte",
            raw={}), stats, set(), None)
        s.commit()
        rows = s.exec(select(Opportunity).where(
            Opportunity.population == "architecte")).all()
        assert len(rows) == 2 and stats.soft_merges == []


def test_stock_places_same_street_number_but_diff_contact_not_merged():
    """FIXTURE ADVERSE inter-masse : deux studios homonymes distincts, même ville
    + même numéro de voie, mais téléphones/domaines différents. Entre deux sources
    de masse (aucune annuaire/insta), le géo seul NE corrobore PAS -> 2 fiches."""
    with Session(_engine()) as s:
        _seed(s, LeadCandidate(
            source="sirene_stock", source_ref="11111111100011",
            establishment_name="Atelier Volume", city="Paris",
            address="10 rue Alpha 75001 Paris",
            main_signal="prescripteur actif", detection_date=date(2026, 7, 11),
            establishment_type="architecte d'intérieur", population="architecte",
            siren="111111111", siret="11111111100011",
            siren_match_method="source"))
        stats = IngestStats(source="places")
        _process_candidate(s, LeadCandidate(
            source="places", source_ref="places:gp3",
            establishment_name="Atelier Volume", city="Paris",
            address="10 avenue Beta 75001 Paris",   # MÊME numéro de voie (10)
            website="https://atelier-volume-place.fr",
            main_signal="prescripteur actif", detection_date=date(2026, 7, 11),
            establishment_type="architecte d'intérieur", population="architecte",
            raw={"phone": "01 99 88 77 66"}), stats, set(), None)
        s.commit()
        rows = s.exec(select(Opportunity).where(
            Opportunity.population == "architecte")).all()
        assert len(rows) == 2 and stats.soft_merges == []


def test_stock_places_same_phone_does_merge():
    """Corroboration FORTE présente (même téléphone normalisé) : la fusion inter-masse
    est bien AUTORISÉE quand un signal fort existe."""
    with Session(_engine()) as s:
        _seed(s, LeadCandidate(
            source="places", source_ref="places:gp4",
            establishment_name="Studio Beton", city="Nantes",
            address="7 rue Kervegan 44000 Nantes",
            website="https://studio-beton.fr",
            main_signal="prescripteur actif", detection_date=date(2026, 7, 11),
            establishment_type="architecte d'intérieur", population="architecte",
            raw={"phone": "02 40 00 11 22"}))
        # Le seed 'places' a posé Opportunity.phone via raw -> support de corroboration.
        opp = s.exec(select(Opportunity)).first()
        assert opp.phone == "02 40 00 11 22"
        stats = IngestStats(source="sirene_stock")
        _process_candidate(s, LeadCandidate(
            source="sirene_stock", source_ref="22222222200022",
            establishment_name="Studio Beton", city="Nantes",
            address="9 rue Autre 44000 Nantes",
            main_signal="prescripteur actif", detection_date=date(2026, 7, 11),
            establishment_type="architecte d'intérieur", population="architecte",
            siren="222222222", siret="22222222200022", siren_match_method="source",
            raw={"phone": "02.40.00.11.22"}), stats, set(), None)  # même tél, format ≠
        s.commit()
        rows = s.exec(select(Opportunity).where(
            Opportunity.population == "architecte")).all()
        assert len(rows) == 1 and stats.soft_merges == [
            ("22222222200022", "places:gp4")]


def test_incoming_instagram_never_triggers_soft_merge():
    """Asymétrie : un lead ENTRANT instagram (hors SOFT_DEDUP_SOURCES) ne déclenche
    jamais la fusion douce, même face à une fiche places corroborable -> A1/CHR
    bit-à-bit intacts (2 fiches)."""
    with Session(_engine()) as s:
        _seed(s, LeadCandidate(
            source="places", source_ref="places:gp5",
            establishment_name="Maison Claire", city="Bordeaux", address="",
            website="https://maisonclaire.fr",
            main_signal="prescripteur actif", detection_date=date(2026, 7, 11),
            establishment_type="architecte d'intérieur", population="architecte",
            raw={"phone": "05 00 11 22 33"}))
        stats = IngestStats(source="instagram")
        _process_candidate(s, LeadCandidate(
            source="instagram", source_ref="maison_claire_insta",
            establishment_name="Maison Claire", city="Bordeaux", address="",
            website="https://maisonclaire.fr",
            main_signal="prescripteur actif", detection_date=date(2026, 7, 11),
            establishment_type="architecte d'intérieur", population="architecte"),
            stats, set(), None)
        s.commit()
        rows = s.exec(select(Opportunity).where(
            Opportunity.population == "architecte")).all()
        assert len(rows) == 2 and stats.soft_merges == []
