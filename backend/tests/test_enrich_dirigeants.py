"""Tests de l'enrichissement des DIRIGEANTS (personnes physiques) via l'API
publique recherche-entreprises.api.gouv.fr.

Couvre SANS RÉSEAU (charges utiles JSON en dur, dict Python = payload) :
formatage d'un dirigeant, extraction PP (holdings/personnes morales ignorées),
sélection DB des fiches cibles (siren présent, dirigeants vide/NULL), et la
passe d'enrichissement (nominal, SIREN inconnu, aucun PP, dry-run, jamais de
ré-écriture d'une fiche déjà remplie). Doctrine VIDE > FAUX : un doute -> rien
n'est écrit."""
from datetime import date

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.ingestion.enrich_dirigeants import (
    DirigeantsStats,
    _enrich_one,
    _format_dirigeant,
    _targets,
    extract_dirigeants_pp,
    run_dirigeants_enrich,
)
from app.models import Opportunity


def _engine():
    e = create_engine("sqlite://")
    SQLModel.metadata.create_all(e)
    return e


def _mk_opp(**kw):
    base = dict(
        establishment_name="Studio X", establishment_type="architecte d'intérieur",
        city="Bordeaux", address="", main_signal="prescripteur actif",
        detection_date=date(2026, 7, 10), estimated_timing="J-90",
        source="sirene_stock", population="architecte",
    )
    base.update(kw)
    return Opportunity(**base)


# --- Formatage d'UN dirigeant (pure) --------------------------------------------


def test_format_dirigeant_avec_qualite():
    d = {"nom": "LASSALLE", "prenoms": "CATHERINE", "qualite": "Gérant",
         "type_dirigeant": "personne physique"}
    assert _format_dirigeant(d) == "Catherine Lassalle, Gérant"


def test_format_dirigeant_sans_qualite():
    d = {"nom": "LASSALLE", "prenoms": "CATHERINE", "qualite": None,
         "type_dirigeant": "personne physique"}
    assert _format_dirigeant(d) == "Catherine Lassalle"


def test_format_dirigeant_titre_meme_si_plusieurs_prenoms():
    d = {"nom": "SIMON", "prenoms": "ALEXANDRE PIERRE JACQUES", "qualite": "Directeur Général"}
    assert _format_dirigeant(d) == "Alexandre Pierre Jacques Simon, Directeur Général"


def test_format_dirigeant_sans_prenom_est_none():
    # Personne morale : pas de "prenoms" -> jamais de nom partiel écrit.
    d = {"siren": "452430416", "denomination": "FINANCIERE BKF", "qualite": "Président de SAS",
         "type_dirigeant": "personne morale"}
    assert _format_dirigeant(d) is None


def test_format_dirigeant_sans_nom_est_none():
    d = {"prenoms": "CATHERINE", "nom": None}
    assert _format_dirigeant(d) is None


# --- Extraction PP depuis la charge utile recherche-entreprises (pure) ----------


def test_extract_nominal_un_dirigeant_pp():
    # Cas prouvé : "CAT LASSALLE INTERIEURS" -> Catherine Lassalle -> catherinelassalle.fr
    data = {
        "siren": "111222333",
        "dirigeants": [
            {"nom": "LASSALLE", "prenoms": "CATHERINE", "qualite": "Gérant",
             "type_dirigeant": "personne physique"},
        ],
    }
    assert extract_dirigeants_pp(data) == ["Catherine Lassalle, Gérant"]


def test_extract_ignore_personne_morale_holding():
    data = {
        "siren": "950026914",
        "dirigeants": [
            {"nom": "BRAYER", "prenoms": "JEAN-PAUL", "qualite": "Président de SAS",
             "type_dirigeant": "personne physique"},
            {"siren": "452430416", "denomination": "FINANCIERE BKF",
             "qualite": "Président de SAS", "type_dirigeant": "personne morale"},
            {"siren": "775726417", "denomination": "KPMG S.A",
             "qualite": "Commissaire aux comptes titulaire", "type_dirigeant": "personne morale"},
        ],
    }
    assert extract_dirigeants_pp(data) == ["Jean-Paul Brayer, Président de SAS"]


def test_extract_seulement_personnes_morales_rend_liste_vide():
    data = {
        "siren": "951379171",
        "dirigeants": [
            {"siren": "433855103", "denomination": "NOCTIS EVENT",
             "qualite": "Président de SAS", "type_dirigeant": "personne morale"},
        ],
    }
    assert extract_dirigeants_pp(data) == []


def test_extract_sans_champ_dirigeants_rend_liste_vide():
    assert extract_dirigeants_pp({"siren": "111222333"}) == []


def test_extract_plusieurs_pp_dans_l_ordre():
    data = {
        "siren": "801838954",
        "dirigeants": [
            {"nom": "BESNAINOU", "prenoms": "PAUL", "qualite": "Gérant",
             "type_dirigeant": "personne physique"},
            {"nom": "ZAGHDOUN", "prenoms": "DAVID", "qualite": "Gérant",
             "type_dirigeant": "personne physique"},
            {"siren": "344366315", "denomination": "ERNST & YOUNG AUDIT",
             "qualite": "Commissaire aux comptes titulaire", "type_dirigeant": "personne morale"},
        ],
    }
    assert extract_dirigeants_pp(data) == ["Paul Besnainou, Gérant", "David Zaghdoun, Gérant"]


# --- Sélection DB des fiches cibles ----------------------------------------------


def test_targets_only_siren_present_and_dirigeants_empty():
    with Session(_engine()) as s:
        s.add(_mk_opp(source_ref="a", siren="111111111", dirigeants=[]))          # cible
        s.add(_mk_opp(source_ref="b", siren=None, dirigeants=[]))                 # pas de siren
        s.add(_mk_opp(source_ref="c", siren="", dirigeants=[]))                   # siren vide
        s.add(_mk_opp(source_ref="d", siren="222222222",
                      dirigeants=["Jean Dupont, Gérant"]))                        # déjà rempli
        s.commit()
        refs = {o.source_ref for o in _targets(s, "architecte", "sirene_stock", 500)}
        assert refs == {"a"}


def test_targets_filters_population_and_source():
    with Session(_engine()) as s:
        s.add(_mk_opp(source_ref="a", siren="111111111", dirigeants=[],
                      population="architecte", source="sirene_stock"))
        s.add(_mk_opp(source_ref="b", siren="222222222", dirigeants=[],
                      population="chr", source="sirene_stock"))                   # autre population
        s.add(_mk_opp(source_ref="c", siren="333333333", dirigeants=[],
                      population="architecte", source="annuaire"))                # autre source
        s.commit()
        refs = {o.source_ref for o in _targets(s, "architecte", "sirene_stock", 500)}
        assert refs == {"a"}


def test_targets_respects_limit():
    with Session(_engine()) as s:
        for i in range(5):
            s.add(_mk_opp(source_ref=str(i), siren=f"11111111{i}", dirigeants=[]))
        s.commit()
        assert len(_targets(s, "architecte", "sirene_stock", 2)) == 2


# --- Passe d'enrichissement d'UNE fiche (doublure sirene, sans réseau) ---------


class _FakeSirene:
    def __init__(self, data):
        self._data = data
        self.calls = []

    def lookup(self, siren):
        self.calls.append(siren)
        return self._data


def test_enrich_one_nominal_ecrit_dirigeants():
    opp = _mk_opp(siren="111222333", dirigeants=[])
    stats = DirigeantsStats()
    sirene = _FakeSirene({
        "siren": "111222333",
        "dirigeants": [{"nom": "LASSALLE", "prenoms": "CATHERINE", "qualite": "Gérant",
                        "type_dirigeant": "personne physique"}],
    })
    _enrich_one(opp, sirene, stats)
    assert opp.dirigeants == ["Catherine Lassalle, Gérant"]
    assert stats.enriched == 1
    assert stats.no_person == 0


def test_enrich_one_siren_inconnu_aucun_resultat():
    opp = _mk_opp(siren="999999999", dirigeants=[])
    stats = DirigeantsStats()
    sirene = _FakeSirene(None)
    _enrich_one(opp, sirene, stats)
    assert opp.dirigeants == []
    assert stats.no_person == 1
    assert stats.enriched == 0


def test_enrich_one_siren_ne_correspond_pas_ne_touche_rien():
    # VIDE > FAUX : la charge renvoyée porte un AUTRE siren (repli fuzzy de
    # l'API sur une recherche texte) -> on ne fait pas confiance à ses dirigeants.
    opp = _mk_opp(siren="111222333", dirigeants=[])
    stats = DirigeantsStats()
    sirene = _FakeSirene({
        "siren": "999888777",
        "dirigeants": [{"nom": "X", "prenoms": "Y", "type_dirigeant": "personne physique"}],
    })
    _enrich_one(opp, sirene, stats)
    assert opp.dirigeants == []
    assert stats.no_person == 1


def test_enrich_one_aucun_dirigeant_personne_physique():
    opp = _mk_opp(siren="951379171", dirigeants=[])
    stats = DirigeantsStats()
    sirene = _FakeSirene({
        "siren": "951379171",
        "dirigeants": [{"siren": "433855103", "denomination": "NOCTIS EVENT",
                        "type_dirigeant": "personne morale"}],
    })
    _enrich_one(opp, sirene, stats)
    assert opp.dirigeants == []
    assert stats.no_person == 1
    assert stats.enriched == 0


def test_enrich_one_dry_run_ne_modifie_pas_la_fiche():
    opp = _mk_opp(siren="111222333", dirigeants=[])
    stats = DirigeantsStats()
    sirene = _FakeSirene({
        "siren": "111222333",
        "dirigeants": [{"nom": "LASSALLE", "prenoms": "CATHERINE",
                        "type_dirigeant": "personne physique"}],
    })
    _enrich_one(opp, sirene, stats, dry_run=True)
    assert opp.dirigeants == []          # rien d'écrit en mémoire
    assert stats.enriched == 1           # mais compté (aperçu du run réel)


# --- Run complet (intégration, fake sirene injecté via monkeypatch) ------------


class _FakeSireneBySiren:
    """Double de SireneEnricher : renvoie une charge utile selon le siren
    demandé, ou lève pour simuler une panne réseau ponctuelle (fail-soft)."""

    def __init__(self, payloads):
        self._payloads = payloads

    def lookup(self, siren):
        val = self._payloads.get(siren, "__missing__")
        if val == "__missing__":
            return None
        if val == "__boom__":
            raise ConnectionError("timeout")
        return val


def test_run_enrich_nominal_commit_et_stats(monkeypatch):
    import app.ingestion.enrich_dirigeants as ed

    payloads = {
        "111111111": {
            "siren": "111111111",
            "dirigeants": [{"nom": "LASSALLE", "prenoms": "CATHERINE", "qualite": "Gérant",
                            "type_dirigeant": "personne physique"}],
        },
    }
    monkeypatch.setattr(ed, "SireneEnricher", lambda **kw: _FakeSireneBySiren(payloads))

    engine = _engine()
    with Session(engine) as s:
        s.add(_mk_opp(source_ref="a", siren="111111111", dirigeants=[]))
        s.commit()

    with Session(engine) as s:
        stats = run_dirigeants_enrich(population="architecte", source="sirene_stock",
                                      limit=500, session=s)
        assert (stats.scanned, stats.enriched, stats.no_person, stats.errors) == (1, 1, 0, 0)

    with Session(engine) as s:
        opp = s.exec(select(Opportunity).where(Opportunity.source_ref == "a")).one()
        assert opp.dirigeants == ["Catherine Lassalle, Gérant"]


def test_run_enrich_siren_inconnu_ne_touche_rien(monkeypatch):
    import app.ingestion.enrich_dirigeants as ed
    monkeypatch.setattr(ed, "SireneEnricher", lambda **kw: _FakeSireneBySiren({}))

    engine = _engine()
    with Session(engine) as s:
        s.add(_mk_opp(source_ref="a", siren="999999999", dirigeants=[]))
        s.commit()

    with Session(engine) as s:
        stats = run_dirigeants_enrich(population="architecte", source="sirene_stock",
                                      limit=500, session=s)
        assert (stats.scanned, stats.enriched, stats.no_person) == (1, 0, 1)

    with Session(engine) as s:
        opp = s.exec(select(Opportunity).where(Opportunity.source_ref == "a")).one()
        assert opp.dirigeants == []


def test_run_enrich_erreur_reseau_est_fail_soft_et_continue(monkeypatch):
    # Une fiche en erreur ne bloque jamais le run : les suivantes sont traitées.
    import app.ingestion.enrich_dirigeants as ed
    payloads = {
        "111111111": "__boom__",
        "222222222": {
            "siren": "222222222",
            "dirigeants": [{"nom": "DUPONT", "prenoms": "JEAN",
                            "type_dirigeant": "personne physique"}],
        },
    }
    monkeypatch.setattr(ed, "SireneEnricher", lambda **kw: _FakeSireneBySiren(payloads))

    engine = _engine()
    with Session(engine) as s:
        s.add(_mk_opp(source_ref="a", siren="111111111", dirigeants=[]))
        s.add(_mk_opp(source_ref="b", siren="222222222", dirigeants=[]))
        s.commit()

    with Session(engine) as s:
        stats = run_dirigeants_enrich(population="architecte", source="sirene_stock",
                                      limit=500, session=s)
        assert stats.scanned == 2
        assert stats.errors == 1
        assert stats.enriched == 1

    with Session(engine) as s:
        b = s.exec(select(Opportunity).where(Opportunity.source_ref == "b")).one()
        assert b.dirigeants == ["Jean Dupont"]


def test_run_enrich_dry_run_ne_commit_rien(monkeypatch):
    import app.ingestion.enrich_dirigeants as ed
    payloads = {
        "111111111": {
            "siren": "111111111",
            "dirigeants": [{"nom": "LASSALLE", "prenoms": "CATHERINE",
                            "type_dirigeant": "personne physique"}],
        },
    }
    monkeypatch.setattr(ed, "SireneEnricher", lambda **kw: _FakeSireneBySiren(payloads))

    engine = _engine()
    with Session(engine) as s:
        s.add(_mk_opp(source_ref="a", siren="111111111", dirigeants=[]))
        s.commit()

    with Session(engine) as s:
        stats = run_dirigeants_enrich(population="architecte", source="sirene_stock",
                                      limit=500, session=s, dry_run=True)
        assert stats.enriched == 1

    with Session(engine) as s:
        opp = s.exec(select(Opportunity).where(Opportunity.source_ref == "a")).one()
        assert opp.dirigeants == []          # dry-run : rien persisté


def test_run_enrich_ne_re_touche_pas_une_fiche_deja_remplie(monkeypatch):
    import app.ingestion.enrich_dirigeants as ed
    monkeypatch.setattr(ed, "SireneEnricher", lambda **kw: _FakeSireneBySiren({}))

    engine = _engine()
    with Session(engine) as s:
        s.add(_mk_opp(source_ref="a", siren="111111111",
                      dirigeants=["Jean Dupont, Gérant"]))
        s.commit()

    with Session(engine) as s:
        stats = run_dirigeants_enrich(population="architecte", source="sirene_stock",
                                      limit=500, session=s)
        assert stats.scanned == 0    # jamais ciblée : dirigeants déjà présents
