"""CLI de balayage repechage (Brique C, phase scan) — aucun reseau, aucune
ecriture dans chr_signal_radar.db (magasin separe teste via tmp_path)."""
from __future__ import annotations

import json
from datetime import date

from sqlmodel import Session, SQLModel, create_engine

from app.ingestion.repechage_scan import (
    AmbiguStore, _v1, _v2_rejection_reason, evaluate_ambigu, run_repechage_scan,
)
from app.models import Opportunity


def _engine():
    e = create_engine("sqlite://")
    SQLModel.metadata.create_all(e)
    return e


def _etab(siret, naf, denom, etat="A", created="2010-01-01", cp="69001", ville="LYON"):
    return {
        "siret": siret, "siren": siret[:9],
        "uniteLegale": {"denominationUniteLegale": denom},
        "periodesEtablissement": [{
            "etatAdministratifEtablissement": etat,
            "activitePrincipaleEtablissement": naf,
        }],
        "dateCreationEtablissement": created,
        "adresseEtablissement": {"codePostalEtablissement": cp,
                                 "libelleCommuneEtablissement": ville},
    }


# --- _v1 (filtre LARGE historique) --------------------------------------------------


def test_v1_positive_keyword_qualifies():
    assert _v1("GARRIGOS DESIGN") is True
    assert _v1("STUDIO PANGO") is True
    assert _v1("BERNARD CANNAVACCIUOLO AGENCEMENTS") is True


def test_v1_negative_keyword_rejects_even_with_positive():
    assert _v1("STUDIO GRAPHIQUE") is False  # "studio" + neg "graphique" -> False
    assert _v1("WEB DESIGN") is False


def test_v1_empty_or_nd_rejected():
    assert _v1("") is False
    assert _v1(None) is False
    assert _v1("[ND]") is False


def test_v1_no_positive_keyword_rejected():
    assert _v1("MENUISERIE MARTIN") is False


# --- _v2_rejection_reason (categorise EXACTEMENT la branche qui a fait echouer
#     `jeunes_studios.qualifies`, meme constantes/ordre, sans reecrire son verdict) --


def test_v2_reason_hard_neg():
    assert _v2_rejection_reason("L'ATELIER ENSEIGNES") == "hard_neg"
    assert _v2_rejection_reason("STUDIO GRAPHIQUE") == "hard_neg"


def test_v2_reason_word_neg():
    assert _v2_rejection_reason("WEB STUDIO") == "word_neg"


def test_v2_reason_agencement_sans_interieur():
    assert _v2_rejection_reason("BERNARD CANNAVACCIUOLO AGENCEMENTS") == "agencement_sans_interieur"


def test_v2_reason_sans_marqueur_interieur():
    assert _v2_rejection_reason("GARRIGOS DESIGN") == "sans_marqueur_interieur"
    assert _v2_rejection_reason("STUDIO PANGO") == "sans_marqueur_interieur"


def test_v2_reason_qualifie_when_marker_present():
    assert _v2_rejection_reason("STUDIO BABA INTERIEURS") == "qualifie"


# --- evaluate_ambigu (fonction PURE, coeur de la classification) --------------------


def test_71_11z_never_ambigu_even_with_keywords():
    # Le 71.11Z reste hors-cible par construction (archi batiment, VIDE > FAUX),
    # meme avec des mots-cles v1 plausibles.
    e = _etab("1", "71.11Z", "AGENCE D INTERIEUR DESIGN")
    assert evaluate_ambigu(e, date(2026, 7, 17)) is None


def test_closed_never_ambigu():
    e = _etab("2", "74.10Z", "GARRIGOS DESIGN", etat="F")
    assert evaluate_ambigu(e, date(2026, 7, 17)) is None


def test_nd_never_ambigu():
    e = _etab("3", "74.10Z", "[ND]")
    e["uniteLegale"] = {"denominationUniteLegale": "[ND]"}
    assert evaluate_ambigu(e, date(2026, 7, 17)) is None


def test_v1_rejected_is_a_true_reject_not_an_ambigu():
    # Rejete DEJA par le filtre v1 large -> pas un "gris", un vrai rejet.
    e = _etab("4", "74.10Z", "MENUISERIE MARTIN")
    assert evaluate_ambigu(e, date(2026, 7, 17)) is None


def test_already_qualified_by_v2_is_not_an_ambigu():
    # Qualifie par v2 -> devient une opportunite normale via sirene_stock,
    # jamais un ambigu (pas de double-comptage).
    e = _etab("5", "74.10Z", "STUDIO BABA INTERIEURS")
    assert evaluate_ambigu(e, date(2026, 7, 17)) is None


def test_hard_neg_confirmed_false_friend_is_not_an_ambigu():
    e = _etab("6", "74.10Z", "L'ATELIER ENSEIGNES")
    assert evaluate_ambigu(e, date(2026, 7, 17)) is None


def test_word_neg_confirmed_false_friend_is_not_an_ambigu():
    e = _etab("7", "74.10Z", "WEB STUDIO")
    assert evaluate_ambigu(e, date(2026, 7, 17)) is None


def test_sans_marqueur_interieur_is_the_ambigu_residual():
    e = _etab("8", "74.10Z", "GARRIGOS DESIGN", cp="75011", ville="PARIS")
    rec = evaluate_ambigu(e, date(2026, 7, 17))
    assert rec is not None
    assert rec.siret == "8" and rec.siren == "8"[:9]
    assert rec.denomination == "GARRIGOS DESIGN"
    assert rec.raison_rejet_v2 == "sans_marqueur_interieur"
    assert rec.cp == "75011" and rec.ville == "Paris"  # _address titre la ville (sirene_delta._title)
    assert rec.naf == "74.10Z"
    assert rec.detection_date == "2026-07-17"


def test_agencement_sans_interieur_is_an_ambigu():
    e = _etab("9", "74.10Z", "BERNARD CANNAVACCIUOLO AGENCEMENTS")
    rec = evaluate_ambigu(e, date(2026, 7, 17))
    assert rec is not None and rec.raison_rejet_v2 == "agencement_sans_interieur"


def test_dirigeant_filled_from_unite_legale_when_denomination_absent():
    e = _etab("10", "74.10Z", None)
    e["uniteLegale"] = {
        "prenom1UniteLegale": "marie", "nomUniteLegale": "studio",
    }
    # denomination = "Marie Studio" (prenom+nom, titre) -> "studio" v1-positif,
    # aucun marqueur interieur -> ambigu, dirigeant rempli.
    rec = evaluate_ambigu(e, date(2026, 7, 17))
    assert rec is not None
    assert rec.dirigeant == "Marie Studio"


# --- AmbiguStore (magasin separe sqlite3 nu) ----------------------------------------


def test_store_insert_and_dedup_by_siret(tmp_path):
    store = AmbiguStore(str(tmp_path / "ambigus.db"))
    rec = evaluate_ambigu(_etab("11", "74.10Z", "GARRIGOS DESIGN"), date(2026, 7, 17))
    assert store.save_candidate(rec) is True   # premiere insertion
    assert store.save_candidate(rec) is False  # doublon (meme siret) -> ignore
    assert store.count() == 1
    store.close()


def test_store_checkpoint_roundtrip(tmp_path):
    store = AmbiguStore(str(tmp_path / "ambigus.db"))
    assert store.get_checkpoint("france") == ("*", False)  # aucun checkpoint -> depart
    store.save_checkpoint("france", "c2", False)
    assert store.get_checkpoint("france") == ("c2", False)
    store.save_checkpoint("france", "", True)
    assert store.get_checkpoint("france") == ("", True)
    store.close()


def test_store_never_touches_main_engine(tmp_path):
    # Le magasin est un fichier sqlite INDEPENDANT — aucune table 'opportunities'.
    store = AmbiguStore(str(tmp_path / "ambigus.db"))
    tables = [r[0] for r in store._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    assert "opportunities" not in tables
    assert {"ambigus", "checkpoint", "verify_verdicts"} <= set(tables)
    store.close()


# --- Extension VERIFICATION du magasin (list_unverified/save_verdict/---------------
#     verdict_counts, consommes par repechage_verify.py) ----------------------------


def test_list_unverified_returns_all_never_verified(tmp_path):
    store = AmbiguStore(str(tmp_path / "ambigus.db"))
    rec1 = evaluate_ambigu(_etab("501", "74.10Z", "GARRIGOS DESIGN"), date(2026, 7, 17))
    rec2 = evaluate_ambigu(_etab("502", "74.10Z", "STUDIO PANGO"), date(2026, 7, 17))
    store.save_candidate(rec1)
    store.save_candidate(rec2)
    unverified = store.list_unverified()
    assert {r.siret for r in unverified} == {"501", "502"}
    store.close()


def test_list_unverified_excludes_confirme_and_infirme_but_retries_sans_site(tmp_path):
    store = AmbiguStore(str(tmp_path / "ambigus.db"))
    for siret, denom in (("601", "GARRIGOS DESIGN"), ("602", "STUDIO PANGO"),
                         ("603", "AGENCEMENT MARTIN")):
        store.save_candidate(evaluate_ambigu(_etab(siret, "74.10Z", denom), date(2026, 7, 17)))
    store.save_verdict("601", "confirme", "https://garrigos.fr", ["interieur"], "A1_content", "2026-07-17")
    store.save_verdict("602", "infirme", "https://pango.fr", [], "site_sans_marqueur_interieur", "2026-07-17")
    store.save_verdict("603", "sans_site", None, [], "no_candidate", "2026-07-17")

    unverified = store.list_unverified()
    assert {r.siret for r in unverified} == {"603"}  # 601/602 definitifs, 603 reessaye
    store.close()


def test_list_unverified_respects_limit_and_ordering(tmp_path):
    store = AmbiguStore(str(tmp_path / "ambigus.db"))
    for i in range(5):
        siret = str(700 + i)
        store.save_candidate(evaluate_ambigu(_etab(siret, "74.10Z", "GARRIGOS DESIGN"), date(2026, 7, 17)))
    rows = store.list_unverified(limit=2)
    assert [r.siret for r in rows] == ["700", "701"]
    store.close()


def test_save_verdict_upserts_by_siret(tmp_path):
    store = AmbiguStore(str(tmp_path / "ambigus.db"))
    store.save_candidate(evaluate_ambigu(_etab("801", "74.10Z", "GARRIGOS DESIGN"), date(2026, 7, 17)))
    store.save_verdict("801", "sans_site", None, [], "no_candidate", "2026-07-17")
    store.save_verdict("801", "confirme", "https://garrigos.fr", ["interieur"], "A1_content", "2026-07-18")

    row = store._conn.execute(
        "SELECT verdict, website, marqueurs, verified_date FROM verify_verdicts WHERE siret = ?",
        ("801",),
    ).fetchone()
    assert row[0] == "confirme"
    assert row[1] == "https://garrigos.fr"
    assert json.loads(row[2]) == ["interieur"]
    assert row[3] == "2026-07-18"
    assert store.count() == 1  # une seule ligne 'ambigus', pas de doublon
    store.close()


def test_verdict_counts_tallies_by_verdict(tmp_path):
    store = AmbiguStore(str(tmp_path / "ambigus.db"))
    for siret, denom in (("901", "GARRIGOS DESIGN"), ("902", "STUDIO PANGO"),
                         ("903", "AGENCEMENT MARTIN")):
        store.save_candidate(evaluate_ambigu(_etab(siret, "74.10Z", denom), date(2026, 7, 17)))
    store.save_verdict("901", "confirme", "https://a.fr", ["interieur"], "A1_content", "2026-07-17")
    store.save_verdict("902", "confirme", "https://b.fr", ["interieur"], "A1_content", "2026-07-17")
    store.save_verdict("903", "infirme", "https://c.fr", [], "site_sans_marqueur_interieur", "2026-07-17")
    assert store.verdict_counts() == {"confirme": 2, "infirme": 1}
    store.close()


# --- run_repechage_scan (orchestration : fetch factice, dedup lecture seule, --------
#     checkpoint, commit par candidat) ------------------------------------------------


def _fake_fetch_page(records, next_cursor):
    def fetch(url, params, headers):
        return {
            "header": {"statut": 200, "total": len(records), "curseurSuivant": next_cursor},
            "etablissements": records,
        }
    return fetch


def test_scan_isolates_ambigus_and_persists_to_store(tmp_path, monkeypatch):
    monkeypatch.setenv("INSEE_API_KEY", "x")
    records = [
        _etab("101", "74.10Z", "GARRIGOS DESIGN"),        # ambigu
        _etab("102", "74.10Z", "STUDIO BABA INTERIEURS"), # deja qualifie -> pas ambigu
        _etab("103", "74.10Z", "L'ATELIER ENSEIGNES"),    # faux-ami confirme -> pas ambigu
        _etab("104", "71.11Z", "AGENCE D ARCHITECTURE"),  # hors-cible -> pas ambigu
        _etab("105", "74.10Z", "MENUISERIE MARTIN"),      # vrai rejet v1 -> pas ambigu
    ]
    fetch = _fake_fetch_page(records, "")  # stock epuise apres cette page

    store_path = str(tmp_path / "ambigus.db")
    with Session(_engine()) as session:  # base principale vide -> rien a dedupliquer
        stats = run_repechage_scan(
            limit=5000, departments=["69"], store_path=store_path,
            session=session, fetch=fetch,
        )

    assert stats.fetched == 5
    assert stats.ambigus_new == 1
    assert stats.naf_71_skipped == 1
    assert stats.not_ambigu == 3  # deja qualifie + faux-ami confirme + vrai rejet v1
    assert stats.deduped_existing == 0
    assert stats.errors == 0
    assert stats.done is True  # curseurSuivant == "" -> stock epuise

    store = AmbiguStore(store_path)
    assert store.count() == 1
    row = store._conn.execute("SELECT siret, raison_rejet_v2 FROM ambigus").fetchone()
    assert row == ("101", "sans_marqueur_interieur")
    store.close()


def test_scan_dedups_against_existing_opportunities_read_only(tmp_path, monkeypatch):
    monkeypatch.setenv("INSEE_API_KEY", "x")
    records = [_etab("201", "74.10Z", "GARRIGOS DESIGN")]
    fetch = _fake_fetch_page(records, "")

    engine = _engine()
    with Session(engine) as session:
        # Meme siren DEJA present en base (une autre source, ex. places) ->
        # ne doit JAMAIS redevenir un ambigu (dedup en lecture seule).
        session.add(Opportunity(
            establishment_name="Garrigos", establishment_type="architecte d'intérieur",
            city="Lyon", address="", main_signal="prescripteur actif",
            estimated_timing="J-90", detection_date=date(2026, 7, 17),
            source="places", population="architecte", siren="201",
        ))
        session.commit()

    store_path = str(tmp_path / "ambigus.db")
    with Session(engine) as session:
        stats = run_repechage_scan(
            limit=5000, store_path=store_path, session=session, fetch=fetch,
        )
        # Lecture seule : aucune ecriture dans la base principale.
        assert len(session.new) == 0 and len(session.dirty) == 0

    assert stats.ambigus_new == 0
    assert stats.deduped_existing == 1

    store = AmbiguStore(store_path)
    assert store.count() == 0
    store.close()


def test_scan_checkpoint_resume_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("INSEE_API_KEY", "x")
    store_path = str(tmp_path / "ambigus.db")
    records = [_etab("301", "74.10Z", "GARRIGOS DESIGN")]

    with Session(_engine()) as session:
        # limit=1 : une seule page consommee (fetch_stock_etablissements
        # s'arrete des que len(out)>=limit) -> next_cursor='cNEXT' represente
        # une TRONCATURE (encore du stock), pas un epuisement.
        stats1 = run_repechage_scan(
            limit=1, store_path=store_path, session=session,
            fetch=_fake_fetch_page(records, "cNEXT"),
        )
    assert stats1.ambigus_new == 1
    assert stats1.done is False
    assert stats1.next_cursor == "cNEXT"

    # Reprise : le checkpoint a avance -> le prochain appel doit repartir de
    # 'cNEXT' (verifie via le parametre curseur recu par `fetch`).
    seen_cursors = []

    def fetch2(url, params, headers):
        seen_cursors.append(params["curseur"])
        return {
            "header": {"statut": 200, "total": 1, "curseurSuivant": ""},
            "etablissements": [_etab("302", "74.10Z", "STUDIO PANGO")],
        }

    with Session(_engine()) as session:
        stats2 = run_repechage_scan(
            limit=5000, store_path=store_path, session=session, fetch=fetch2,
        )
    assert seen_cursors == ["cNEXT"]
    assert stats2.ambigus_new == 1
    assert stats2.done is True

    store = AmbiguStore(store_path)
    assert store.count() == 2  # 101/301 + 302, jamais de doublon
    store.close()


def test_scan_skips_when_checkpoint_already_done(tmp_path, monkeypatch):
    monkeypatch.setenv("INSEE_API_KEY", "x")
    store_path = str(tmp_path / "ambigus.db")
    store = AmbiguStore(store_path)
    store.save_checkpoint("france", "", True)
    store.close()

    called = {"n": 0}

    def fetch(url, params, headers):
        called["n"] += 1
        return {"header": {"statut": 200, "total": 0, "curseurSuivant": ""}, "etablissements": []}

    with Session(_engine()) as session:
        stats = run_repechage_scan(limit=5000, store_path=store_path, session=session, fetch=fetch)

    assert stats.done is True
    assert called["n"] == 0  # aucun appel reseau : stock deja epuise selon le checkpoint


def test_scan_forced_cursor_overrides_checkpoint_done(tmp_path, monkeypatch):
    monkeypatch.setenv("INSEE_API_KEY", "x")
    store_path = str(tmp_path / "ambigus.db")
    store = AmbiguStore(store_path)
    store.save_checkpoint("france", "", True)
    store.close()

    def fetch(url, params, headers):
        assert params["curseur"] == "*"
        return {"header": {"statut": 200, "total": 0, "curseurSuivant": ""}, "etablissements": []}

    with Session(_engine()) as session:
        stats = run_repechage_scan(limit=5000, store_path=store_path, session=session,
                                   fetch=fetch, cursor="*")
    assert stats.fetched == 0


def test_scan_commits_per_candidate_error_isolated(tmp_path, monkeypatch):
    # Un enregistrement brut malforme (pas de dict periodesEtablissement
    # exploitable) ne doit jamais faire echouer tout le run.
    monkeypatch.setenv("INSEE_API_KEY", "x")
    records = [
        {"siret": "bad"},  # malforme -> pas d'exception attendue (per=[{}]) mais couvre le chemin
        _etab("401", "74.10Z", "GARRIGOS DESIGN"),
    ]
    fetch = _fake_fetch_page(records, "")
    store_path = str(tmp_path / "ambigus.db")
    with Session(_engine()) as session:
        stats = run_repechage_scan(limit=5000, store_path=store_path, session=session, fetch=fetch)
    assert stats.errors == 0  # etat par defaut 'A', naf None -> not_ambigu, pas une erreur
    assert stats.not_ambigu == 1
    assert stats.ambigus_new == 1
