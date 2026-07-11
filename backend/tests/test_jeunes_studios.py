# backend/tests/test_jeunes_studios.py
"""Connecteur délta jeunes studios (A2, T3) — mapping PUR, aucun réseau.
Grounded sur le rendement mesuré par la sonde (sonde-a2.json volet 2)."""
from datetime import date

from app.ingestion.jeunes_studios import (
    ARCHI_NAF_CODES, JeunesStudiosConnector, map_jeune_studio, qualifies,
)

TODAY = date(2026, 7, 11)


def _etab(naf="71.11Z", enseigne=None, denom=None, prenom=None, nom=None,
          siret="12345678900011", created="2026-06-20", etat="A", nd=False):
    ul = {}
    if denom:
        ul["denominationUniteLegale"] = denom
    if prenom:
        ul["prenom1UniteLegale"] = prenom
    if nom:
        ul["nomUniteLegale"] = nom
    per = {"etatAdministratifEtablissement": etat,
           "activitePrincipaleEtablissement": naf}
    if enseigne:
        per["enseigne1Etablissement"] = "[ND]" if nd else enseigne
    return {"siret": siret, "siren": siret[:9], "uniteLegale": ul,
            "periodesEtablissement": [per], "etablissementSiege": True,
            "dateCreationEtablissement": created,
            "adresseEtablissement": {"libelleCommuneEtablissement": "PARIS",
                                     "codePostalEtablissement": "75011"}}


def test_qualifies_keyword_hit():
    assert qualifies("STUDIO GHIRIBELLI")
    assert qualifies("Le Gambit Architecture d'Interieur")
    assert qualifies("ATELIER EL MANSOURY")


def test_qualifies_rejects_empty_and_neg_keyword():
    assert not qualifies("")
    assert not qualifies("[ND]")
    assert not qualifies("SIXCOM")                       # pas de mot métier
    assert not qualifies("LEA LAXTON DESIGN GRAPHIQUE")  # 74.10Z graphisme (garde neg)


def test_map_qualified_studio():
    etab = _etab(denom="MANOA DESIGN", siret="99988877700022", created="2026-06-25")
    c = map_jeune_studio(etab, TODAY)
    assert c is not None
    assert c.source == "jeunes_studios" and c.source_ref == "99988877700022"
    assert c.population == "architecte"
    assert c.lifecycle_label == "unknown"
    assert c.main_signal == "prescripteur actif"
    assert "jeune studio (création récente)" in c.secondary_signals
    assert c.siren == "999888777" and c.naf == "71.11Z"
    assert c.siren_match_method == "source"
    assert c.activity_start_date == date(2026, 6, 25)


def test_map_personne_physique_sets_decision_maker():
    etab = _etab(denom=None, prenom="Camille", nom="Durand")
    # Personne physique nommée SANS mot-clé métier -> non qualifiée (sonde #9).
    assert map_jeune_studio(etab, TODAY) is None
    etab2 = _etab(denom=None, prenom="Camille", nom="Durand",
                  enseigne="STUDIO CAMILLE DESIGN")
    c = map_jeune_studio(etab2, TODAY)
    assert c is not None and c.decision_maker == "Camille Durand"


def test_map_drops_masked_closed_and_nonarchi():
    assert map_jeune_studio(_etab(denom="STUDIO X", etat="F"), TODAY) is None
    assert map_jeune_studio(_etab(denom="STUDIO X", naf="56.10A"), TODAY) is None
    # Dénomination masquée [ND] partout -> injoignable ET inqualifiable.
    masked = _etab(denom=None, enseigne="STUDIO Y", nd=True)
    assert map_jeune_studio(masked, TODAY) is None


def test_connector_fetch_uses_archi_naf_and_no_future(monkeypatch):
    monkeypatch.setenv("INSEE_API_KEY", "test-key")
    captured = {}

    def fake_fetch_new(date_from, date_to, naf_codes, cp_prefixes=None,
                       limit=3000, fetch=None, meta=None):
        captured["naf"] = list(naf_codes)
        captured["date_to"] = date_to
        captured["cp"] = cp_prefixes
        if meta is not None:
            meta["total"] = 1625
        return [_etab(denom="MANOA DESIGN")]

    import app.ingestion.jeunes_studios as js
    monkeypatch.setattr(js, "fetch_new_etablissements", fake_fetch_new)
    conn = JeunesStudiosConnector()
    records = conn.fetch(since_days=30, limit=1000)
    assert captured["naf"] == ARCHI_NAF_CODES
    assert captured["date_to"] == date.today()  # PAS d'horizon futur
    assert captured["cp"] is None               # France entière par défaut
    assert conn.last_total_count == 1625
    cands = conn.to_candidates(records)
    assert len(cands) == 1 and cands[0].source == "jeunes_studios"
