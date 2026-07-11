"""Éval annuaires/délta (A2, T5) — offline. Gate 0 faux merge annuaire×insta.

Aucun réseau ni LLM : http_fetch injecté (fixtures = extraits des HTML sondés),
matcher/sirene factices. La métrique `false_merges_annuaire_insta` est nourrie des
paires RÉELLEMENT fusionnées par le pipeline (`stats.soft_merges`), pas d'une
entrée fabriquée."""
from datetime import date

from sqlmodel import Session, SQLModel, create_engine, select

from app.ingestion.base import LeadCandidate
from app.ingestion.eval.prescripteurs_metrics import false_merges_annuaire_insta
from app.ingestion.eval.prescripteurs_run import run_annuaires_gate
from app.ingestion.pipeline import IngestStats, _process_candidate, run_annuaires
from app.models import Opportunity


def _engine():
    e = create_engine("sqlite://")
    SQLModel.metadata.create_all(e)
    return e


class _NoSirene:
    def lookup(self, siren):
        return None


def test_false_merges_metric_pure():
    # Vérité : (annuaire, insta) sont le MÊME studio -> pas un faux merge.
    truth_same = {("cfai:12", "metropole_concept")}
    pairs = [("cfai:12", "metropole_concept"), ("cfai:99", "autre_studio")]
    fm = false_merges_annuaire_insta(pairs, truth_same)
    assert fm == [("cfai:99", "autre_studio")]  # merge non justifié = faux merge


def test_false_merges_metric_all_justified_is_empty():
    truth_same = {("cfai:12", "metropole_concept")}
    assert false_merges_annuaire_insta([("cfai:12", "metropole_concept")], truth_same) == []
    assert false_merges_annuaire_insta([], truth_same) == []


def test_run_annuaires_no_false_merge_on_distinct_homonym():
    # Deux "Atelier Design" à Lyon (homonymes distincts) : l'annuaire NE fusionne
    # PAS (dédup renvoie None sur >=2) -> 0 faux merge, 2 fiches conservées.
    LIST = ('<table class="table-list"><tbody><tr><td>69001</td><td>LYON</td>'
            '<td><b>NOUVEAU Atelier</b></td><td>Atelier Design</td>'
            '<td><a href="/annuaire-professionnel/adherent/50"></a></td></tr>'
            '</tbody></table><span class="badge bg-secondary">1 résultats</span>')
    FICHE = ('<header><h1>Paul NOUVEAU</h1><p class="member-company">Atelier Design'
             '</p></header><h3>Adresse</h3><div class="details-group">1 rue X 69001 LYON</div>')
    pages = {"https://www.cfai.fr/fr/recherche/annuaire-professionnel?page=1": LIST,
             "https://www.cfai.fr/annuaire-professionnel/adherent/50": FICHE}
    with Session(_engine()) as s:
        for ref in ("insta_a", "insta_b"):
            _process_candidate(s, LeadCandidate(
                source="instagram", source_ref=ref, establishment_name="Atelier Design",
                city="Lyon", address="", main_signal="prescripteur actif",
                detection_date=date(2026, 7, 11),
                establishment_type="architecte d'intérieur", population="architecte"),
                IngestStats(source="instagram"), set(), None)
        s.commit()
        stats = run_annuaires("cfai", limit=10, session=s,
                              http_fetch=lambda u: pages.get(u),
                              matcher=lambda **k: None, sirene=_NoSirene())
        # 2 Insta + 1 annuaire = 3 fiches (aucune fusion abusive sur homonyme).
        rows = s.exec(select(Opportunity).where(
            Opportunity.population == "architecte")).all()
        assert len(rows) == 3
        # Aucune fusion émise -> le gate 0 faux merge est nourri du vrai signal.
        assert stats.soft_merges == []
        assert false_merges_annuaire_insta(stats.soft_merges, set()) == []


def test_run_annuaires_legit_merge_is_not_flagged():
    # Couple annuaire×insta LÉGITIME (même studio, corroboré par le domaine de site)
    # -> une fusion RÉELLE émise, mais annotée "même studio" -> PAS un faux merge.
    LIST = ('<table class="table-list"><tbody><tr><td>75015</td><td>PARIS</td>'
            '<td><b>ALEZRA Franck</b></td><td>Metropole Concept</td>'
            '<td><a href="/annuaire-professionnel/adherent/12"></a></td></tr>'
            '</tbody></table><span class="badge bg-secondary">1 résultats</span>')
    FICHE = ('<header><h1>Franck ALEZRA</h1><p class="member-company">Metropole Concept'
             '</p></header><h3>Adresse</h3><div class="details-group">13 rue X 75015 PARIS</div>'
             '<h3>Site</h3><div class="details-group">'
             '<a href="http://www.metropole-concept.com">site</a></div>')
    pages = {"https://www.cfai.fr/fr/recherche/annuaire-professionnel?page=1": LIST,
             "https://www.cfai.fr/annuaire-professionnel/adherent/12": FICHE}
    with Session(_engine()) as s:
        _process_candidate(s, LeadCandidate(
            source="instagram", source_ref="metropole_concept",
            establishment_name="Metropole Concept", city="Paris", address="",
            website="https://metropole-concept.com",
            main_signal="prescripteur actif", detection_date=date(2026, 7, 11),
            establishment_type="architecte d'intérieur", population="architecte"),
            IngestStats(source="instagram"), set(), None)
        s.commit()
        stats = run_annuaires("cfai", limit=10, session=s,
                              http_fetch=lambda u: pages.get(u),
                              matcher=lambda **k: None, sirene=_NoSirene())
        # Une seule fiche archi (fusion réelle) et la paire est tracée.
        rows = s.exec(select(Opportunity).where(
            Opportunity.population == "architecte")).all()
        assert len(rows) == 1
        assert stats.soft_merges == [("cfai:12", "metropole_concept")]
        # Annotée "même studio" -> aucun faux merge.
        truth = {("cfai:12", "metropole_concept")}
        assert false_merges_annuaire_insta(stats.soft_merges, truth) == []


def test_run_annuaires_gate_on_shipped_fixtures():
    # Le gate offline LIVRÉ (fixtures annuaires_snapshots/) tourne de bout en bout :
    # un couple légitime (à ne pas flagger) + un homonyme distinct (à ne pas fusionner)
    # -> 0 faux merge, et >=70 % des membres annuaire -> studio_actif.
    res = run_annuaires_gate()
    assert res["false_merges"] == []
    assert res["gate_zero_false_merge"] is True
    assert res["studio_actif_rate"] >= 0.70
    assert res["gate_annuaire_studio_actif"] is True
