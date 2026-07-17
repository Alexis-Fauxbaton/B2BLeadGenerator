"""run_annuaires (A2, T4) — orchestration sans réseau (http_fetch + matcher +
sirene injectés). Vérifie enrichissement SIREN, dédup nom+ville (0 faux merge)."""
from datetime import date

from sqlmodel import Session, SQLModel, create_engine, select

from app.ingestion.base import LeadCandidate
from app.ingestion.enrichment.siret_matcher import MatchResult
from app.ingestion.pipeline import (
    IngestStats, _connector_key, _process_candidate, _soft_dedup_architecte,
    run_annuaires,
)
from app.models import Opportunity


def _engine():
    e = create_engine("sqlite://")
    SQLModel.metadata.create_all(e)
    return e


CFAI_LIST = """<table class="table-list"><tbody>
<tr><td>75015</td><td>PARIS</td><td><b>ALEZRA Franck</b></td>
<td>SARL METROPOLE CONCEPT</td><td><a href="/annuaire-professionnel/adherent/12"></a></td></tr>
</tbody></table><span class="badge bg-secondary">1 résultats</span>"""
CFAI_FICHE = """<header><h1>Franck ALEZRA</h1>
<p class="member-company">SARL METROPOLE CONCEPT</p></header>
<h3>Adresse</h3><div class="details-group">13 rue Mademoiselle 75015 PARIS</div>
<h3>Site</h3><div class="details-group">
<a href="http://www.metropole-concept.com">site</a></div>"""


def test_run_annuaires_enriches_siren_and_dirigeant(monkeypatch):
    pages = {
        "https://www.cfai.fr/fr/recherche/annuaire-professionnel?page=1": CFAI_LIST,
        "https://www.cfai.fr/annuaire-professionnel/adherent/12": CFAI_FICHE,
    }

    def matcher(name, city=None, postal=None, website=None, context=None, **k):
        return MatchResult(siren="500600700", siret="50060070000011", naf="71.11Z",
                           enseigne="METROPOLE CONCEPT", confidence="haute",
                           method="nom", date_creation="2015-03-01")

    class _Sirene:
        def lookup(self, siren):
            return {"dirigeants": [{"prenoms": "Franck", "nom": "Alezra"}],
                    "siege": {"date_creation": "2015-03-01"}}

    with Session(_engine()) as s:
        stats = run_annuaires("cfai", limit=10, session=s,
                              http_fetch=lambda u: pages.get(u),
                              matcher=matcher, sirene=_Sirene())
        assert stats.created == 1
        opp = s.exec(select(Opportunity).where(Opportunity.source == "annuaire")).first()
        assert opp is not None
        assert opp.population == "architecte" and opp.siren == "500600700"
        assert opp.decision_maker == "Franck Alezra"
        assert opp.activity_start_date == date(2015, 3, 1)
        assert opp.lifecycle_label == "studio_actif"
        # Contact annuaire natif (site publié par le membre) = fiable -> affiché en UI
        # (pas "à trouver"). Sinon les téléphones UFDI / emails CFAI resteraient cachés.
        assert opp.contact_confidence == "haute"


def test_soft_dedup_exact_one_match_with_corroboration():
    with Session(_engine()) as s:
        # Studio Insta existant (source instagram, sans SIREN) AVEC un site.
        _process_candidate(s, LeadCandidate(
            source="instagram", source_ref="metropole_concept",
            establishment_name="Metropole Concept", city="Paris", address="",
            website="http://www.metropole-concept.com",
            main_signal="prescripteur actif", detection_date=date(2026, 7, 11),
            establishment_type="architecte d'intérieur", population="architecte"),
            IngestStats(source="instagram"), set(), None)
        s.commit()
        # Même nom+ville ET même domaine de site -> corroboration OK.
        incoming = LeadCandidate(
            source="annuaire", source_ref="cfai:12",
            establishment_name="Metropole Concept", city="Paris", address="",
            website="https://metropole-concept.com",
            main_signal="prescripteur actif", detection_date=date(2026, 7, 11),
            establishment_type="architecte d'intérieur", population="architecte")
        match = _soft_dedup_architecte(s, incoming)
        assert match is not None and match.source == "instagram"


def test_soft_dedup_name_city_only_without_corroboration_returns_none():
    # Homonyme fortuit : même nom+ville mais AUCUN signal commun -> pas de merge
    # (nom+ville nécessaire mais pas suffisant, finding revue).
    with Session(_engine()) as s:
        _process_candidate(s, LeadCandidate(
            source="instagram", source_ref="studio_x_insta",
            establishment_name="Studio X", city="Paris", address="",
            main_signal="prescripteur actif", detection_date=date(2026, 7, 11),
            establishment_type="architecte d'intérieur", population="architecte"),
            IngestStats(source="instagram"), set(), None)
        s.commit()
        incoming = LeadCandidate(
            source="annuaire", source_ref="cfai:77",
            establishment_name="Studio X", city="Paris", address="",
            website="https://un-autre-studio-x.fr",
            main_signal="prescripteur actif", detection_date=date(2026, 7, 11),
            establishment_type="architecte d'intérieur", population="architecte")
        assert _soft_dedup_architecte(s, incoming) is None


def test_soft_dedup_two_matches_returns_none_no_false_merge():
    with Session(_engine()) as s:
        for ref in ("a", "b"):
            _process_candidate(s, LeadCandidate(
                source="instagram", source_ref=ref, establishment_name="Atelier Design",
                city="Lyon", address="", main_signal="prescripteur actif",
                detection_date=date(2026, 7, 11),
                establishment_type="architecte d'intérieur", population="architecte"),
                IngestStats(source="instagram"), set(), None)
        s.commit()
        incoming = LeadCandidate(
            source="annuaire", source_ref="cfai:99", establishment_name="Atelier Design",
            city="Lyon", address="", main_signal="prescripteur actif",
            detection_date=date(2026, 7, 11),
            establishment_type="architecte d'intérieur", population="architecte")
        # 2 homonymes -> pas de merge (vide/2 fiches > faux merge).
        assert _soft_dedup_architecte(s, incoming) is None


def test_annuaire_incoming_merges_into_insta_by_name_city():
    with Session(_engine()) as s:
        # Fiche Insta existante avec un numéro de voie (support de corroboration).
        _process_candidate(s, LeadCandidate(
            source="instagram", source_ref="studio_lumen",
            establishment_name="Studio Lumen", city="Bordeaux",
            address="12 rue des Faures 33000 Bordeaux",
            main_signal="prescripteur actif", detection_date=date(2026, 7, 11),
            establishment_type="architecte d'intérieur", population="architecte"),
            IngestStats(source="instagram"), set(), None)
        s.commit()
        stats = IngestStats(source="annuaire")
        # Même nom+ville ET même NUMÉRO DE VOIE -> corroboration OK ; le site
        # annuaire comble un trou. (Le CP seul ne corrobore plus : impliqué par la
        # garde ville, il ne discrimine pas deux studios homonymes d'une commune.)
        _process_candidate(s, LeadCandidate(
            source="annuaire", source_ref="ufdi:studio-lumen-1",
            establishment_name="Studio Lumen", city="Bordeaux",
            address="12 rue des Faures 33000 Bordeaux",
            main_signal="prescripteur actif", detection_date=date(2026, 7, 11),
            establishment_type="architecte d'intérieur", population="architecte",
            website="https://studiolumen.fr"),
            stats, set(), None)
        s.commit()
        # Une seule fiche (fusion), enrichie du site annuaire.
        rows = s.exec(select(Opportunity).where(
            Opportunity.population == "architecte")).all()
        assert len(rows) == 1
        assert rows[0].website == "https://studiolumen.fr"
        assert stats.updated == 1
        # La paire fusionnée est tracée (alimente le gate 0 faux merge, T5).
        assert stats.soft_merges == [("ufdi:studio-lumen-1", "studio_lumen")]


def test_shared_social_host_does_not_corroborate_no_false_merge():
    # Lentille VIDE>FAUX (A2) : deux studios DISTINCTS, même nom+ville, chacun
    # listant SA page Facebook. Le premier label 'facebook' est mutualisé -> ne
    # doit PAS corroborer (sinon faux merge indépendant de la géo).
    with Session(_engine()) as s:
        _process_candidate(s, LeadCandidate(
            source="instagram", source_ref="studio_horizon_insta",
            establishment_name="Studio Horizon", city="Paris", address="",
            website="https://facebook.com/StudioHorizonRiveGauche",
            main_signal="prescripteur actif", detection_date=date(2026, 7, 11),
            establishment_type="architecte d'intérieur", population="architecte"),
            IngestStats(source="instagram"), set(), None)
        s.commit()
        incoming = LeadCandidate(
            source="annuaire", source_ref="cfai:501",
            establishment_name="Studio Horizon", city="Paris", address="",
            website="https://facebook.com/StudioHorizon75011",
            main_signal="prescripteur actif", detection_date=date(2026, 7, 11),
            establishment_type="architecte d'intérieur", population="architecte")
        assert _soft_dedup_architecte(s, incoming) is None


def test_same_postal_different_street_does_not_corroborate():
    # Le CP seul (impliqué par la garde ville) ne corrobore plus : deux studios
    # distincts, même nom+ville+CP mais rues/numéros différents -> pas de merge.
    with Session(_engine()) as s:
        _process_candidate(s, LeadCandidate(
            source="instagram", source_ref="atelier_clair_insta",
            establishment_name="Atelier Clair", city="Annecy",
            address="12 rue de la Gare 74000 Annecy",
            main_signal="prescripteur actif", detection_date=date(2026, 7, 11),
            establishment_type="architecte d'intérieur", population="architecte"),
            IngestStats(source="instagram"), set(), None)
        s.commit()
        incoming = LeadCandidate(
            source="annuaire", source_ref="cfai:502",
            establishment_name="Atelier Clair", city="Annecy",
            address="5 avenue du Parmelan 74000 Annecy",
            main_signal="prescripteur actif", detection_date=date(2026, 7, 11),
            establishment_type="architecte d'intérieur", population="architecte")
        assert _soft_dedup_architecte(s, incoming) is None


def test_run_annuaires_cfai_fills_phone_on_creation():
    # Régression : la fiche CFAI (téléphone en clair, "Téléphones/fax") doit
    # remplir Opportunity.phone à la création, comme UFDI/Places (raw['phone']
    # — même contrat, cf. pipeline._process_candidate). 728 fiches CFAI en base
    # avaient 0 téléphone faute de ce report cand.raw['phone'] -> opp.phone.
    pages = {
        "https://www.cfai.fr/fr/recherche/annuaire-professionnel?page=1": CFAI_LIST,
        "https://www.cfai.fr/annuaire-professionnel/adherent/12": (
            CFAI_FICHE.replace(
                "<h3>Site</h3>",
                '<h3>Téléphones/fax</h3><div class="details-group">'
                '01 53 68 91 80</div><h3>Site</h3>',
            )
        ),
    }

    def matcher(name, city=None, postal=None, website=None, context=None, **k):
        return None  # pas de SIREN nécessaire pour ce test

    with Session(_engine()) as s:
        stats = run_annuaires("cfai", limit=10, session=s,
                              http_fetch=lambda u: pages.get(u),
                              matcher=matcher, sirene=None)
        assert stats.created == 1
        opp = s.exec(select(Opportunity).where(Opportunity.source == "annuaire")).first()
        assert opp is not None
        assert opp.phone == "01 53 68 91 80"
        # Contact natif (téléphone publié par le membre) = fiable -> pas
        # "à trouver" en UI (même doctrine que le site/email CFAI, décision #9/#10).
        assert opp.contact_confidence == "haute"


def test_run_annuaires_cfai_update_fills_empty_phone_without_overwriting():
    # La branche UPDATE (même source+source_ref+population) doit combler un
    # Opportunity.phone vide au re-passage CFAI (728 fiches déjà en base avant
    # le fix) -- sans jamais écraser un téléphone déjà renseigné par ailleurs
    # (doctrine : jamais d'écrasement d'un champ rempli).
    with Session(_engine()) as s:
        # 1er passage (pré-fix simulé) : candidate SANS téléphone -> ligne créée,
        # phone vide.
        _process_candidate(s, LeadCandidate(
            source="annuaire", source_ref="cfai:12",
            establishment_name="SARL METROPOLE CONCEPT", city="Paris", address="",
            main_signal="prescripteur actif", detection_date=date(2026, 7, 11),
            establishment_type="architecte d'intérieur", population="architecte"),
            IngestStats(source="annuaire"), set(), None)
        s.commit()
        opp = s.exec(select(Opportunity).where(Opportunity.source_ref == "cfai:12")).first()
        assert opp.phone is None

        # 2e passage (post-fix) : même source_ref, cette fois avec le téléphone
        # dans raw -> l'UPDATE doit le combler.
        _process_candidate(s, LeadCandidate(
            source="annuaire", source_ref="cfai:12",
            establishment_name="SARL METROPOLE CONCEPT", city="Paris", address="",
            main_signal="prescripteur actif", detection_date=date(2026, 7, 11),
            establishment_type="architecte d'intérieur", population="architecte",
            raw={"phone": "01 53 68 91 80"}),
            IngestStats(source="annuaire"), set(), None)
        s.commit()
        s.refresh(opp)
        assert opp.phone == "01 53 68 91 80"

        # 3e passage : un téléphone DIFFÉRENT (ex. erreur de source amont) ne doit
        # JAMAIS écraser le téléphone déjà en base.
        _process_candidate(s, LeadCandidate(
            source="annuaire", source_ref="cfai:12",
            establishment_name="SARL METROPOLE CONCEPT", city="Paris", address="",
            main_signal="prescripteur actif", detection_date=date(2026, 7, 11),
            establishment_type="architecte d'intérieur", population="architecte",
            raw={"phone": "01 99 99 99 99"}),
            IngestStats(source="annuaire"), set(), None)
        s.commit()
        s.refresh(opp)
        assert opp.phone == "01 53 68 91 80"


# --- F2 (revue adverse) : deux connecteurs annuaire DIFFÉRENTS partagent tous
# `source='annuaire'` — seul le préfixe de `source_ref` (`cfai:`, `ufdi:`,
# `annuairedecoration:`, ...) les distingue. Le filtre `source != cand.source`
# excluait à tort TOUT le pool 'annuaire' de la fusion douce/corroboration
# SIREN, empêchant deux annuaires de se dédupliquer entre eux (doublon garanti
# pour un même prescripteur listé dans 2 annuaires). Fix : `_connector_key`.

def test_connector_key_distinguishes_annuaire_connectors_by_source_ref_prefix():
    # Deux annuaires -> deux clés différentes malgré `source='annuaire'` identique.
    assert _connector_key("annuaire", "cfai:12") == "cfai"
    assert _connector_key("annuaire", "annuairedecoration:9") == "annuairedecoration"
    assert (_connector_key("annuaire", "cfai:12")
            != _connector_key("annuaire", "ufdi:studio-lumen-1"))
    # Même connecteur, fiches différentes -> même clé.
    assert _connector_key("annuaire", "cfai:12") == _connector_key("annuaire", "cfai:99")
    # Sources non-annuaire : la clé EST la source (comportement historique).
    assert _connector_key("sirene_stock", "11111111100011") == "sirene_stock"
    assert _connector_key("places", "places:gp9") == "places"
    assert _connector_key("instagram", "atelier_nord_insta") == "instagram"


def test_two_different_annuaire_connectors_merge_with_phone_corroboration():
    """(a) même personne dans CFAI puis annuaire_decoration (nom+ville identiques
    + corroboration téléphone) -> 1 SEULE fiche, ENRICHIE par le second passage."""
    with Session(_engine()) as s:
        _process_candidate(s, LeadCandidate(
            source="annuaire", source_ref="cfai:501",
            establishment_name="Studio Meridien", city="Toulouse", address="",
            main_signal="prescripteur actif", detection_date=date(2026, 7, 11),
            establishment_type="architecte d'intérieur", population="architecte",
            raw={"phone": "05 61 00 11 22"}),
            IngestStats(source="annuaire"), set(), None)
        s.commit()
        opp = s.exec(select(Opportunity)).first()
        assert opp.phone == "05 61 00 11 22"

        stats = IngestStats(source="annuaire")
        _process_candidate(s, LeadCandidate(
            source="annuaire", source_ref="annuairedecoration:77",
            establishment_name="Studio Meridien", city="Toulouse", address="",
            website="https://studio-meridien.fr",
            main_signal="prescripteur actif", detection_date=date(2026, 7, 11),
            establishment_type="architecte d'intérieur", population="architecte",
            raw={"phone": "05.61.00.11.22"}),  # même tél, format différent
            stats, set(), None)
        s.commit()

        rows = s.exec(select(Opportunity).where(
            Opportunity.population == "architecte")).all()
        assert len(rows) == 1
        assert rows[0].source_ref == "cfai:501"  # fiche d'origine conservée
        assert rows[0].website == "https://studio-meridien.fr"  # comblé
        assert stats.updated == 1
        assert stats.soft_merges == [("annuairedecoration:77", "cfai:501")]


def test_two_different_annuaire_connectors_homonym_without_corroboration_not_merged():
    """(b) homonyme même ville, SANS aucune corroboration (tél/domaine/adresse/
    dirigeant) -> PAS de fusion, 2 fiches distinctes (vide > faux)."""
    with Session(_engine()) as s:
        _process_candidate(s, LeadCandidate(
            source="annuaire", source_ref="cfai:502",
            establishment_name="Atelier Central", city="Nice", address="",
            main_signal="prescripteur actif", detection_date=date(2026, 7, 11),
            establishment_type="architecte d'intérieur", population="architecte"),
            IngestStats(source="annuaire"), set(), None)
        s.commit()

        stats = IngestStats(source="annuaire")
        _process_candidate(s, LeadCandidate(
            source="annuaire", source_ref="monacomania:88",
            establishment_name="Atelier Central", city="Nice", address="",
            website="https://un-autre-atelier-central.fr",
            main_signal="prescripteur actif", detection_date=date(2026, 7, 11),
            establishment_type="architecte d'intérieur", population="architecte"),
            stats, set(), None)
        s.commit()

        rows = s.exec(select(Opportunity).where(
            Opportunity.population == "architecte")).all()
        assert len(rows) == 2 and stats.soft_merges == []


def test_cfai_recrawl_same_source_ref_updates_not_duplicate_even_with_siren():
    """(c) re-crawl CFAI d'une fiche déjà en base (même source_ref, donc même
    connecteur) -> chemin `existing` (update), jamais la fusion douce ni la
    corroboration SIREN -> aucun doublon, aucun soft_merge enregistré."""
    with Session(_engine()) as s:
        _process_candidate(s, LeadCandidate(
            source="annuaire", source_ref="cfai:503",
            establishment_name="Cabinet Delta", city="Rennes", address="",
            main_signal="prescripteur actif", detection_date=date(2026, 7, 11),
            establishment_type="architecte d'intérieur", population="architecte",
            siren="333333333", siret="33333333300011"),
            IngestStats(source="annuaire"), set(), None)
        s.commit()

        stats = IngestStats(source="annuaire")
        _process_candidate(s, LeadCandidate(
            source="annuaire", source_ref="cfai:503",  # même connecteur, même fiche
            establishment_name="Cabinet Delta", city="Rennes", address="",
            main_signal="prescripteur actif", detection_date=date(2026, 7, 11),
            establishment_type="architecte d'intérieur", population="architecte",
            siren="333333333", siret="33333333300011",
            website="https://cabinet-delta.fr"),
            stats, set(), None)
        s.commit()

        rows = s.exec(select(Opportunity).where(
            Opportunity.population == "architecte")).all()
        assert len(rows) == 1
        assert rows[0].website == "https://cabinet-delta.fr"
        assert stats.soft_merges == []


def test_two_different_annuaire_connectors_merge_by_siren_corroboration():
    """Corroboration SIREN (pas seulement la fusion douce nom+ville) doit aussi
    fonctionner entre deux connecteurs annuaire distincts partageant `source=
    'annuaire'` (même matcher SIREN, deux annuaires) -> 1 fiche, pas 2."""
    with Session(_engine()) as s:
        _process_candidate(s, LeadCandidate(
            source="annuaire", source_ref="ufdi:studio-alpha",
            establishment_name="Studio Alpha", city="Marseille", address="",
            main_signal="prescripteur actif", detection_date=date(2026, 7, 11),
            establishment_type="architecte d'intérieur", population="architecte",
            siren="444444444", siret="44444444400011"),
            IngestStats(source="annuaire"), set(), None)
        s.commit()

        stats = IngestStats(source="annuaire")
        _process_candidate(s, LeadCandidate(
            source="annuaire", source_ref="monarchitecteinterieur:12",
            establishment_name="Studio Alpha SARL", city="Marseille", address="",
            main_signal="prescripteur actif", detection_date=date(2026, 7, 11),
            establishment_type="architecte d'intérieur", population="architecte",
            siren="444444444", siret="44444444400011"),  # même SIREN
            stats, set(), None)
        s.commit()

        rows = s.exec(select(Opportunity).where(
            Opportunity.population == "architecte")).all()
        assert len(rows) == 1 and stats.updated == 1
