"""SireneStockConnector (B1, T2). Aucun reseau -- records injectes."""
from datetime import date

from app.ingestion.sirene_stock import (
    SireneStockConnector, map_stock_etablissement, qualifies_71,
)


def _etab(siret, naf, denom, etat="A", created="2010-01-01"):
    return {"siret": siret, "siren": siret[:9], "uniteLegale": {"denominationUniteLegale": denom},
            "periodesEtablissement": [{"etatAdministratifEtablissement": etat,
                                       "activitePrincipaleEtablissement": naf}],
            "dateCreationEtablissement": created,
            "adresseEtablissement": {"codePostalEtablissement": "69001",
                                     "libelleCommuneEtablissement": "LYON"}}


def test_7410z_keyword_qualifies():
    c = map_stock_etablissement(_etab("11111111100011", "74.10Z", "ATELIER D INTERIEUR"), date(2026, 7, 12))
    assert c is not None and c.source == "sirene_stock" and c.population == "architecte"
    assert c.siren == "111111111" and c.siren_match_method == "source"
    assert "stock sirene" in c.secondary_signals


def test_7410z_false_friend_dropped():
    assert map_stock_etablissement(_etab("2", "74.10Z", "STUDIO DESIGN GRAPHIQUE"), date(2026, 7, 12)) is None


def test_nd_dropped():
    e = _etab("3", "74.10Z", "[ND]")
    e["uniteLegale"] = {"denominationUniteLegale": "[ND]"}
    assert map_stock_etablissement(e, date(2026, 7, 12)) is None


def test_recent_booster_under_18_months():
    c = map_stock_etablissement(_etab("4", "74.10Z", "STUDIO DECO INTERIEUR", "A", created="2026-01-01"), date(2026, 7, 12))
    assert "jeune studio (création récente)" in c.secondary_signals  # < 18 mois
    old = map_stock_etablissement(_etab("5", "74.10Z", "STUDIO DECO INTERIEUR", "A", created="2010-01-01"), date(2026, 7, 12))
    assert "jeune studio (création récente)" not in old.secondary_signals


def test_7410z_gt_false_friends_dropped():
    # Filtre RESSERRÉ (GT stock, VIDE > FAUX) : les faux-amis 74.10Z sans
    # marqueur intérieur (design produit/graphique, enseignes, agenceur…) écartés.
    for name in ["EDDS DESIGN", "GARRIGOS DESIGN", "STUDIO PANGO",
                 "L'ATELIER ENSEIGNES", "MATIERES ET DECORATION",
                 "BERNARD CANNAVACCIUOLO AGENCEMENTS", "MACOM STUDIO"]:
        assert map_stock_etablissement(_etab("9", "74.10Z", name), date(2026, 7, 12)) is None, name


def test_7410z_gt_survivors_kept():
    # Dénominations SÛRES avec marqueur intérieur : conservées.
    for name in ["STUDIO BABA INTERIEURS", "ELYTE HOME",
                 "MARINE HILAIRE ARCHITECTURE D'INTERIEUR"]:
        assert map_stock_etablissement(_etab("9", "74.10Z", name), date(2026, 7, 12)) is not None, name


def test_71_11z_requires_strict_cooccurrence():
    assert qualifies_71("CABINET D ARCHITECTURE") is False  # batiment
    assert qualifies_71("ARCHITECTE D INTERIEUR MARTIN") is True  # co-occ archi+interieur
    assert map_stock_etablissement(_etab("6", "71.11Z", "AGENCE D ARCHITECTURE"), date(2026, 7, 12)) is None


def test_closed_dropped():
    assert map_stock_etablissement(_etab("7", "74.10Z", "STUDIO DECO", etat="F"), date(2026, 7, 12)) is None


def test_connector_fetch_sets_cursor_and_total():
    conn = SireneStockConnector()

    def fake_fetch(naf, cp_prefixes=None, limit=0, cursor="*", fetch=None, meta=None):
        if meta is not None:
            meta["total"] = 42
        return [_etab("8", "74.10Z", "STUDIO DECO INTERIEUR")], "cNEXT"

    import app.ingestion.sirene_stock as m
    m.fetch_stock_etablissements = fake_fetch  # monkeypatch simple
    recs = conn.fetch(departments=["69"], limit=8000)
    assert conn.last_total_count == 42 and conn.last_cursor == "cNEXT"
    assert conn.to_candidates(recs)[0].establishment_name == "STUDIO DECO INTERIEUR"
