"""Tests du connecteur delta-Sirene (INSEE) — brique 2 du pivot."""
from datetime import date

from app.ingestion.insee import build_query, fetch_new_etablissements
from app.ingestion.sirene_delta import CHR_NAF_CODES, map_etablissement

D1, D2 = date(2026, 6, 29), date(2026, 7, 5)
NAFS = ["56.10A", "56.10C"]


def test_build_query_periode_and_range():
    q = build_query(D1, D2, NAFS, None)
    # Verifie la syntaxe validee live le 2026-07-06 : plage + periode(...).
    assert "dateCreationEtablissement:[2026-06-29 TO 2026-07-05]" in q
    assert ("periode(activitePrincipaleEtablissement:56.10A"
            " OR activitePrincipaleEtablissement:56.10C)") in q
    assert " AND " in q


def test_build_query_cp_prefixes():
    q = build_query(D1, D2, NAFS, ["75", "92"])
    assert "(codePostalEtablissement:75* OR codePostalEtablissement:92*)" in q


class _FakeInsee:
    """Fetch factice paginee : rejoue des pages INSEE et enregistre les appels."""
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def __call__(self, url, params, headers):
        self.calls.append(dict(params))
        cur = params.get("curseur", "*")
        page = self.pages.get(cur, {"header": {"statut": 404}})
        return page


def _page(etabs, curseur, suivant, total):
    return {"header": {"statut": 200, "total": total, "curseur": curseur,
                       "curseurSuivant": suivant},
            "etablissements": etabs}


def test_fetch_paginates_with_curseur(monkeypatch):
    monkeypatch.setenv("INSEE_API_KEY", "test-key")
    fake = _FakeInsee({
        "*": _page([{"siret": "1"}, {"siret": "2"}], "*", "CUR2", 3),
        "CUR2": _page([{"siret": "3"}], "CUR2", "CUR2", 3),  # suivant == curseur -> fin
    })
    got = fetch_new_etablissements(D1, D2, NAFS, fetch=fake)
    assert [e["siret"] for e in got] == ["1", "2", "3"]
    assert fake.calls[0]["curseur"] == "*"
    assert fake.calls[1]["curseur"] == "CUR2"


def test_fetch_respects_limit_and_fails_soft(monkeypatch):
    monkeypatch.setenv("INSEE_API_KEY", "test-key")
    fake = _FakeInsee({"*": _page([{"siret": str(i)} for i in range(5)], "*", "N", 99),
                       "N": {"header": {"statut": 500}}})
    assert len(fetch_new_etablissements(D1, D2, NAFS, limit=2, fetch=fake)) == 2
    # Erreur en page 2 -> on garde la premiere page (fail-soft, jamais d'exception).
    got = fetch_new_etablissements(D1, D2, NAFS, limit=100, fetch=fake)
    assert [e["siret"] for e in got] == ["0", "1", "2", "3", "4"]


def test_fetch_without_key_is_noop(monkeypatch):
    monkeypatch.delenv("INSEE_API_KEY", raising=False)
    fake = _FakeInsee({})
    assert fetch_new_etablissements(D1, D2, NAFS, fetch=fake) == []
    assert fake.calls == []


TODAY = date(2026, 7, 6)

# Etablissement diffusible, societe, cree (forme reelle API 3.11).
ETAB_OK = {
    "siren": "105506737", "siret": "10550673700029",
    "dateCreationEtablissement": "2026-07-01", "etablissementSiege": False,
    "statutDiffusionEtablissement": "O",
    "uniteLegale": {"denominationUniteLegale": "ACTIVE FOOD CONCEPT LE PUY",
                    "categorieJuridiqueUniteLegale": "5710",
                    "prenom1UniteLegale": None, "nomUniteLegale": None},
    "adresseEtablissement": {"numeroVoieEtablissement": "13",
                             "typeVoieEtablissement": "ROUTE",
                             "libelleVoieEtablissement": "DE COUBON",
                             "codePostalEtablissement": "43700",
                             "libelleCommuneEtablissement": "BRIVES-CHARENSAC"},
    "periodesEtablissement": [{"activitePrincipaleEtablissement": "56.10B",
                               "etatAdministratifEtablissement": "A",
                               "enseigne1Etablissement": None,
                               "denominationUsuelleEtablissement": None}],
}
# Personne physique non-diffusible ([ND] partout, pas d'enseigne).
ETAB_ND = {
    "siren": "100731280", "siret": "10073128000010",
    "dateCreationEtablissement": "2026-07-01", "etablissementSiege": True,
    "statutDiffusionEtablissement": "P",
    "uniteLegale": {"denominationUniteLegale": "[ND]", "nomUniteLegale": "[ND]",
                    "prenom1UniteLegale": "[ND]",
                    "categorieJuridiqueUniteLegale": "1000"},
    "adresseEtablissement": {"codePostalEtablissement": "75011",
                             "libelleCommuneEtablissement": "PARIS"},
    "periodesEtablissement": [{"activitePrincipaleEtablissement": "56.10C",
                               "etatAdministratifEtablissement": "A",
                               "enseigne1Etablissement": None}],
}


def test_map_etablissement_nominal():
    cand = map_etablissement(ETAB_OK, TODAY)
    assert cand is not None
    assert cand.source == "sirene" and cand.source_ref == "10550673700029"
    assert cand.siren == "105506737" and cand.naf == "56.10B"
    assert cand.establishment_name == "ACTIVE FOOD CONCEPT LE PUY"
    assert cand.city == "Brives-Charensac"
    assert cand.address == "13 ROUTE DE COUBON, 43700 Brives-Charensac"
    assert cand.main_signal == "ouverture prochaine"
    assert cand.activity_start_date == date(2026, 7, 1)
    # Etablissement secondaire d'une societe = extension multi-sites.
    assert "extension multi-sites" in cand.secondary_signals


def test_map_enseigne_prime_sur_denomination():
    etab = {**ETAB_OK, "periodesEtablissement": [{
        **ETAB_OK["periodesEtablissement"][0], "enseigne1Etablissement": "CHEZ LUCIE"}]}
    cand = map_etablissement(etab, TODAY)
    assert cand.establishment_name == "CHEZ LUCIE"


def test_map_nd_sans_enseigne_est_ecarte():
    assert map_etablissement(ETAB_ND, TODAY) is None


def test_map_nd_avec_enseigne_est_garde():
    etab = {**ETAB_ND, "periodesEtablissement": [{
        **ETAB_ND["periodesEtablissement"][0], "enseigne1Etablissement": "SNACK 11E"}]}
    cand = map_etablissement(etab, TODAY)
    assert cand is not None and cand.establishment_name == "SNACK 11E"


def test_map_creation_future_marquee_pre_declaree():
    etab = {**ETAB_OK, "dateCreationEtablissement": "2026-09-15"}
    cand = map_etablissement(etab, TODAY)
    # La date declaree AU FUTUR = ouverture annoncee au registre (signal fort).
    assert "2026-09-15" in (cand.proof_text or "")
    assert "pré-déclarée" in (cand.proof_text or "")


def test_map_etat_ferme_est_ecarte():
    etab = {**ETAB_OK, "periodesEtablissement": [{
        **ETAB_OK["periodesEtablissement"][0], "etatAdministratifEtablissement": "F"}]}
    assert map_etablissement(etab, TODAY) is None


def test_connector_fetch_window_and_future(monkeypatch):
    """La fenetre couvre [today-since_days ; today+FUTURE_HORIZON_DAYS] :
    le passe recent ET les ouvertures pre-declarees."""
    import app.ingestion.sirene_delta as sd
    captured = {}

    # Fake "ancien style" qui accepte `meta` mais ne le remplit jamais (bypass) :
    # le connecteur doit alors se rabattre sur len(records) pour last_total_count.
    def fake_fetch(date_from, date_to, naf_codes, cp_prefixes=None, limit=3000,
                   fetch=None, meta=None):
        captured.update(date_from=date_from, date_to=date_to,
                        naf_codes=list(naf_codes), cp=cp_prefixes, limit=limit)
        return [dict(ETAB_OK)]

    monkeypatch.setattr(sd, "fetch_new_etablissements", fake_fetch)
    conn = sd.SireneDeltaConnector()
    records = conn.fetch(since_days=7, limit=500, departments=["75", "92"])
    assert len(records) == 1 and conn.last_total_count == 1
    assert (captured["date_to"] - captured["date_from"]).days == 7 + sd.FUTURE_HORIZON_DAYS
    assert captured["naf_codes"] == sd.CHR_NAF_CODES
    assert captured["cp"] == ["75", "92"] and captured["limit"] == 500


def test_connector_to_candidates_filters_unusable():
    import app.ingestion.sirene_delta as sd
    conn = sd.SireneDeltaConnector()
    cands = conn.to_candidates([dict(ETAB_OK), dict(ETAB_ND)])
    assert len(cands) == 1 and cands[0].source_ref == "10550673700029"


def test_connector_registered_in_pipeline():
    from app.ingestion.pipeline import get_connector
    conn = get_connector("sirene")
    assert conn.name == "sirene"


def test_map_etablissement_poses_siret_et_methode_source():
    """Le SIRET vient de la source elle-meme (pas d'un matching) ; methode 'source'."""
    cand = map_etablissement(ETAB_OK, TODAY)
    assert cand.siret == "10550673700029"
    assert cand.siren_match_method == "source"


def test_match_lead_exposes_tracabilite(monkeypatch):
    import app.ingestion.pipeline as pl
    from app.ingestion.enrichment.siret_matcher import MatchResult

    monkeypatch.setattr(pl, "match_siret", lambda **kw: MatchResult(
        siren="989119201", siret="98911920100011", naf="56.10C",
        enseigne="OCOIN", confidence="moyenne", method="arbitre"))
    got = pl._match_lead({"handle": "x", "name": "Tre Gusto", "city": "Sartrouville"})
    assert got["siret"] == "98911920100011"
    assert got["method"] == "arbitre" and got["confidence"] == "moyenne"


def test_process_candidate_persists_tracabilite(tmp_path):
    from sqlmodel import SQLModel, Session, create_engine, select
    from app.models import Opportunity
    from app.ingestion.base import LeadCandidate
    from app.ingestion.pipeline import _process_candidate, IngestStats
    from datetime import date as _d

    engine = create_engine(f"sqlite:///{tmp_path/'t.db'}")
    SQLModel.metadata.create_all(engine)
    cand = LeadCandidate(
        source="sirene", source_ref="10550673700029",
        establishment_name="ACTIVE FOOD CONCEPT", city="Brives-Charensac",
        main_signal="ouverture prochaine", detection_date=_d(2026, 7, 6),
        classification_text="restaurant", establishment_type="restaurant",
        siren="105506737", naf="56.10B", siret="10550673700029",
    )
    with Session(engine) as s:
        _process_candidate(s, cand, IngestStats(source="sirene"), set(), None)
        s.commit()
        opp = s.exec(select(Opportunity)).first()
        assert opp.siret == "10550673700029"


def test_fusion_par_siren_cross_source(tmp_path):
    """Un lead sirene entrant dont le SIREN existe deja cote instagram
    FUSIONNE (pas de doublon) : signal ajoute, corroboration, rescore."""
    from sqlmodel import SQLModel, Session, create_engine, select
    from app.models import Opportunity, Signal
    from app.ingestion.base import LeadCandidate
    from app.ingestion.pipeline import _process_candidate, IngestStats
    from datetime import date as _d

    engine = create_engine(f"sqlite:///{tmp_path/'t.db'}")
    SQLModel.metadata.create_all(engine)
    insta = LeadCandidate(
        source="instagram", source_ref="tregusto_sartrouville",
        establishment_name="Tre Gusto", city="Sartrouville",
        main_signal="ouverture prochaine", detection_date=_d(2026, 7, 5),
        classification_text="restaurant", establishment_type="restaurant",
        siren="989119201", instagram="tregusto_sartrouville",
    )
    sirene = LeadCandidate(
        source="sirene", source_ref="98911920100011",
        establishment_name="OCOIN", city="Sartrouville",
        main_signal="ouverture prochaine", detection_date=_d(2026, 7, 6),
        classification_text="restaurant", establishment_type="restaurant",
        siren="989119201", siret="98911920100011",
        address="143 AVENUE GENERAL DE GAULLE, 78500 Sartrouville",
    )
    stats = IngestStats(source="test")
    with Session(engine) as s:
        _process_candidate(s, insta, stats, set(), None)
        _process_candidate(s, sirene, stats, set(), None)
        s.commit()
        opps = s.exec(select(Opportunity)).all()
        assert len(opps) == 1  # fusion, pas de doublon
        opp = opps[0]
        assert opp.source == "instagram"          # la fiche d'origine est conservee
        assert opp.instagram == "tregusto_sartrouville"
        assert opp.siret == "98911920100011"      # complete par le registre
        assert opp.address                         # adresse registre posee
        assert "corroboré registre × instagram" in (opp.secondary_signals or [])
        signals = s.exec(select(Signal)).all()
        assert len(signals) == 2                   # 1 par provenance
        assert stats.created == 1 and stats.updated == 1


def test_pas_de_fusion_sans_siren(tmp_path):
    """Deux sources sans SIREN commun -> deux fiches (comportement inchange)."""
    from sqlmodel import SQLModel, Session, create_engine, select
    from app.models import Opportunity
    from app.ingestion.base import LeadCandidate
    from app.ingestion.pipeline import _process_candidate, IngestStats
    from datetime import date as _d

    engine = create_engine(f"sqlite:///{tmp_path/'t.db'}")
    SQLModel.metadata.create_all(engine)
    a = LeadCandidate(source="instagram", source_ref="h1", establishment_name="A",
                      city="Paris", main_signal="ouverture prochaine",
                      detection_date=_d(2026, 7, 6), classification_text="restaurant",
                      establishment_type="restaurant")
    b = LeadCandidate(source="sirene", source_ref="s1", establishment_name="B",
                      city="Paris", main_signal="ouverture prochaine",
                      detection_date=_d(2026, 7, 6), classification_text="restaurant",
                      establishment_type="restaurant", siren="111222333")
    with Session(engine) as s:
        st = IngestStats(source="test")
        _process_candidate(s, a, st, set(), None)
        _process_candidate(s, b, st, set(), None)
        s.commit()
        assert len(s.exec(select(Opportunity)).all()) == 2


# --- Revue finale B2 : fixes 1, 2, 3, 4, 6, 7, 8, 9 --------------------------


def test_reprocessed_sirene_candidate_no_duplicate_signal(tmp_path):
    """[Fix 1] Rejouer le meme candidat sirene (delta quotidien) sur une fiche
    deja fusionnee ne doit PAS ajouter un second Signal ni re-incrementer
    stats.updated : la fusion a deja eu lieu, il n'y a rien a refaire."""
    from sqlmodel import SQLModel, Session, create_engine, select
    from app.models import Opportunity, Signal
    from app.ingestion.base import LeadCandidate
    from app.ingestion.pipeline import _process_candidate, IngestStats
    from datetime import date as _d

    engine = create_engine(f"sqlite:///{tmp_path/'t.db'}")
    SQLModel.metadata.create_all(engine)
    insta = LeadCandidate(
        source="instagram", source_ref="tregusto_sartrouville",
        establishment_name="Tre Gusto", city="Sartrouville",
        main_signal="ouverture prochaine", detection_date=_d(2026, 7, 5),
        classification_text="restaurant", establishment_type="restaurant",
        siren="989119201", instagram="tregusto_sartrouville",
    )
    sirene = LeadCandidate(
        source="sirene", source_ref="98911920100011",
        establishment_name="OCOIN", city="Sartrouville",
        main_signal="ouverture prochaine", detection_date=_d(2026, 7, 6),
        classification_text="restaurant", establishment_type="restaurant",
        siren="989119201", siret="98911920100011",
        address="143 AVENUE GENERAL DE GAULLE, 78500 Sartrouville",
    )
    stats = IngestStats(source="test")
    with Session(engine) as s:
        _process_candidate(s, insta, stats, set(), None)
        _process_candidate(s, sirene, stats, set(), None)
        # Re-fetch quotidien du delta INSEE : le meme candidat sirene revient.
        sirene_again = LeadCandidate(
            source="sirene", source_ref="98911920100011",
            establishment_name="OCOIN", city="Sartrouville",
            main_signal="ouverture prochaine", detection_date=_d(2026, 7, 7),
            classification_text="restaurant", establishment_type="restaurant",
            siren="989119201", siret="98911920100011",
            address="143 AVENUE GENERAL DE GAULLE, 78500 Sartrouville",
        )
        _process_candidate(s, sirene_again, stats, set(), None)
        s.commit()
        assert len(s.exec(select(Opportunity)).all()) == 1
        signals = s.exec(select(Signal)).all()
        assert len(signals) == 2  # toujours 1 par provenance, pas de 3e au rejeu
        assert stats.created == 1 and stats.updated == 1  # pas re-incremente


def test_fusion_bodacc_sirene_pas_de_tag_instagram_dm_rempli(tmp_path):
    """[Fix 2] Fusion BODACC x Sirene (deux registres, aucun Instagram) :
    PAS de tag 'corrobore registre x instagram' (semantiquement faux et
    score-bearing) ; mais decision_maker/dirigeants sont repris du candidat
    entrant (BODACC apporte une valeur que Sirene n'a pas)."""
    from sqlmodel import SQLModel, Session, create_engine, select
    from app.models import Opportunity
    from app.ingestion.base import LeadCandidate
    from app.ingestion.pipeline import _process_candidate, IngestStats, CORROBORATION_TAG
    from datetime import date as _d

    engine = create_engine(f"sqlite:///{tmp_path/'t.db'}")
    SQLModel.metadata.create_all(engine)
    sirene = LeadCandidate(
        source="sirene", source_ref="98911920100011",
        establishment_name="OCOIN", city="Sartrouville",
        main_signal="ouverture prochaine", detection_date=_d(2026, 7, 6),
        classification_text="restaurant", establishment_type="restaurant",
        siren="989119201", siret="98911920100011",
    )
    bodacc = LeadCandidate(
        source="bodacc", source_ref="bodacc-1",
        establishment_name="OCOIN", city="Sartrouville",
        main_signal="création récente", detection_date=_d(2026, 7, 7),
        classification_text="restaurant", establishment_type="restaurant",
        siren="989119201", decision_maker="Samuel Afif, Président",
        dirigeants=["Samuel Afif, Président"],
    )
    stats = IngestStats(source="test")
    with Session(engine) as s:
        _process_candidate(s, sirene, stats, set(), None)
        _process_candidate(s, bodacc, stats, set(), None)
        s.commit()
        opps = s.exec(select(Opportunity)).all()
        assert len(opps) == 1
        opp = opps[0]
        assert CORROBORATION_TAG not in (opp.secondary_signals or [])
        assert opp.decision_maker == "Samuel Afif, Président"
        assert opp.dirigeants == ["Samuel Afif, Président"]


def test_same_source_reupsert_conserve_le_tag_corroboration(tmp_path):
    """[Fix 3] Un upsert meme-source (ex: Instagram revoit son propre profil)
    sur une fiche deja corroboree ne doit pas effacer le tag posé par la
    fusion precedente."""
    from sqlmodel import SQLModel, Session, create_engine, select
    from app.models import Opportunity
    from app.ingestion.base import LeadCandidate
    from app.ingestion.pipeline import _process_candidate, IngestStats, CORROBORATION_TAG
    from datetime import date as _d

    engine = create_engine(f"sqlite:///{tmp_path/'t.db'}")
    SQLModel.metadata.create_all(engine)
    insta = LeadCandidate(
        source="instagram", source_ref="tregusto_sartrouville",
        establishment_name="Tre Gusto", city="Sartrouville",
        main_signal="ouverture prochaine", detection_date=_d(2026, 7, 5),
        classification_text="restaurant", establishment_type="restaurant",
        siren="989119201", instagram="tregusto_sartrouville",
    )
    sirene = LeadCandidate(
        source="sirene", source_ref="98911920100011",
        establishment_name="OCOIN", city="Sartrouville",
        main_signal="ouverture prochaine", detection_date=_d(2026, 7, 6),
        classification_text="restaurant", establishment_type="restaurant",
        siren="989119201", siret="98911920100011",
    )
    stats = IngestStats(source="test")
    with Session(engine) as s:
        _process_candidate(s, insta, stats, set(), None)
        _process_candidate(s, sirene, stats, set(), None)
        # Upsert meme-source (instagram) de la fiche d'origine : ne connait
        # pas le tag pose par la fusion et ne doit pas l'effacer.
        insta_again = LeadCandidate(
            source="instagram", source_ref="tregusto_sartrouville",
            establishment_name="Tre Gusto", city="Sartrouville",
            main_signal="ouverture prochaine", detection_date=_d(2026, 7, 8),
            classification_text="restaurant", establishment_type="restaurant",
            siren="989119201", instagram="tregusto_sartrouville",
        )
        _process_candidate(s, insta_again, stats, set(), None)
        s.commit()
        opp = s.exec(select(Opportunity)).first()
        assert CORROBORATION_TAG in (opp.secondary_signals or [])


def test_fetch_new_etablissements_meta_recupere_le_total(monkeypatch):
    """[Fix 4] `meta` recoit header.total de la 1re page reussie."""
    monkeypatch.setenv("INSEE_API_KEY", "test-key")
    fake = _FakeInsee({
        "*": _page([{"siret": "1"}, {"siret": "2"}], "*", "CUR2", 42),
        "CUR2": _page([{"siret": "3"}], "CUR2", "CUR2", 42),
    })
    meta: dict = {}
    got = fetch_new_etablissements(D1, D2, NAFS, fetch=fake, meta=meta)
    assert len(got) == 3
    assert meta["total"] == 42


def test_fetch_new_etablissements_page_vide_arrete_la_boucle(monkeypatch):
    """[Fix 9] Garde-fou anti-boucle infinie : une page 200 avec
    etablissements=[] arrete la pagination, meme si curseurSuivant continue
    d'avancer (ne doit jamais atteindre la page suivante)."""
    monkeypatch.setenv("INSEE_API_KEY", "test-key")
    fake = _FakeInsee({
        "*": _page([], "*", "CUR2", 0),
        "CUR2": _page([{"siret": "x"}], "CUR2", "CUR2", 1),
    })
    got = fetch_new_etablissements(D1, D2, NAFS, fetch=fake)
    assert got == []
    assert len(fake.calls) == 1


class _RecordingEnricher:
    """Enrichisseur factice qui journalise les appels (et peut lever pour
    prouver qu'il n'est jamais invoque pour la source a exclure)."""
    def __init__(self, forbid: bool = False):
        self.calls = []
        self.forbid = forbid

    def enrich(self, cand):
        self.calls.append(cand.source)
        if self.forbid:
            raise AssertionError("enricher.enrich() ne doit pas etre appele pour source=sirene")
        return cand

    def lookup(self, siren):
        return None


def test_sirene_candidate_saute_lenrichisseur(tmp_path):
    """[Fix 6] L'enrichissement Sirene est saute pour source='sirene' (donnees
    INSEE deja autoritatives) mais reste applique pour les autres sources."""
    from sqlmodel import SQLModel, Session, create_engine
    from app.ingestion.base import LeadCandidate
    from app.ingestion.pipeline import _process_candidate, IngestStats
    from datetime import date as _d

    engine = create_engine(f"sqlite:///{tmp_path/'t.db'}")
    SQLModel.metadata.create_all(engine)

    forbidding_enricher = _RecordingEnricher(forbid=True)
    sirene = LeadCandidate(
        source="sirene", source_ref="10550673700029",
        establishment_name="ACTIVE FOOD CONCEPT", city="Brives-Charensac",
        main_signal="ouverture prochaine", detection_date=_d(2026, 7, 6),
        classification_text="restaurant", establishment_type="restaurant",
        siren="105506737", naf="56.10B", siret="10550673700029",
    )
    with Session(engine) as s:
        _process_candidate(s, sirene, IngestStats(source="sirene"), set(), forbidding_enricher)
        s.commit()
    assert forbidding_enricher.calls == []  # jamais appele pour sirene

    recording_enricher = _RecordingEnricher(forbid=False)
    bodacc = LeadCandidate(
        source="bodacc", source_ref="bodacc-x",
        establishment_name="Chez Test", city="Paris",
        main_signal="création récente", detection_date=_d(2026, 7, 6),
        classification_text="restaurant", establishment_type="restaurant",
    )
    with Session(engine) as s:
        _process_candidate(s, bodacc, IngestStats(source="bodacc"), set(), recording_enricher)
        s.commit()
    assert recording_enricher.calls == ["bodacc"]  # applique pour les autres sources


def test_siret_mismatch_meme_siren_pas_de_fusion(tmp_path):
    """[Fix 7] Meme SIREN mais SIRET differents des deux cotes = deux
    etablissements distincts de la meme entreprise (chaine multi-sites),
    PAS une fusion (sinon on perdrait un vrai lead d'un site different)."""
    from sqlmodel import SQLModel, Session, create_engine, select
    from app.models import Opportunity
    from app.ingestion.base import LeadCandidate
    from app.ingestion.pipeline import _process_candidate, IngestStats
    from datetime import date as _d

    engine = create_engine(f"sqlite:///{tmp_path/'t.db'}")
    SQLModel.metadata.create_all(engine)
    a = LeadCandidate(
        source="bodacc", source_ref="bodacc-a",
        establishment_name="Chaine A - Site 1", city="Paris",
        main_signal="création récente", detection_date=_d(2026, 7, 6),
        classification_text="restaurant", establishment_type="restaurant",
        siren="111222333", siret="11122233300010",
    )
    b = LeadCandidate(
        source="sirene", source_ref="11122233300028",
        establishment_name="Chaine A - Site 2", city="Lyon",
        main_signal="ouverture prochaine", detection_date=_d(2026, 7, 6),
        classification_text="restaurant", establishment_type="restaurant",
        siren="111222333", siret="11122233300028",
    )
    stats = IngestStats(source="test")
    with Session(engine) as s:
        _process_candidate(s, a, stats, set(), None)
        _process_candidate(s, b, stats, set(), None)
        s.commit()
        assert len(s.exec(select(Opportunity)).all()) == 2  # deux sites, pas de fusion
        assert stats.created == 2


def test_connector_default_departments_idf_et_france(monkeypatch):
    """[Fix 8] `departments=None` -> defaut IdF (aligne sur BODACC) ;
    `["france"]` (valeur speciale) -> cp_prefixes=None (France entiere)."""
    import app.ingestion.sirene_delta as sd
    captured = {}

    def fake_fetch(date_from, date_to, naf_codes, cp_prefixes=None, limit=3000,
                    fetch=None, meta=None):
        captured["cp"] = cp_prefixes
        return []

    monkeypatch.setattr(sd, "fetch_new_etablissements", fake_fetch)
    conn = sd.SireneDeltaConnector()

    conn.fetch(since_days=7, limit=100)
    assert captured["cp"] == sd.IDF_CP_PREFIXES

    conn.fetch(since_days=7, limit=100, departments=["france"])
    assert captured["cp"] is None
