"""insee STOCK (B1, T1) — requete sans fenetre de date, curseur, reprise. Aucun reseau."""
from app.ingestion.insee import build_stock_query, fetch_stock_etablissements


def test_build_stock_query_has_no_date_and_gates_active():
    q = build_stock_query(["74.10Z", "71.11Z"], cp_prefixes=["69"])
    assert "dateCreation" not in q                       # STOCK : pas de fenetre
    assert "etatAdministratifEtablissement:A" in q
    assert "activitePrincipaleEtablissement:74.10Z" in q
    assert "codePostalEtablissement:69*" in q
    assert q.count("periode(") == 1                       # historises sous periode()


def test_fetch_stock_paginates_by_cursor_and_returns_next(monkeypatch):
    monkeypatch.setenv("INSEE_API_KEY", "x")
    pages = [  # 2 pages puis epuisement
        {"header": {"statut": 200, "total": 3, "curseurSuivant": "c2"},
         "etablissements": [{"siret": "1"}, {"siret": "2"}]},
        {"header": {"statut": 200, "total": 3, "curseurSuivant": "c2"},  # == curseur -> stop
         "etablissements": [{"siret": "3"}]},
    ]
    calls = {"i": 0}

    def fake(url, params, headers):
        i = calls["i"]
        calls["i"] += 1
        assert params["curseur"] in ("*", "c2")
        return pages[min(i, len(pages) - 1)]

    recs, nxt = fetch_stock_etablissements(["74.10Z"], cp_prefixes=["69"], limit=8000, fetch=fake)
    assert [r["siret"] for r in recs] == ["1", "2", "3"]
    assert nxt in ("c2", "")   # curseur de reprise expose


def test_fetch_stock_no_key_returns_empty(monkeypatch):
    monkeypatch.delenv("INSEE_API_KEY", raising=False)
    assert fetch_stock_etablissements(["74.10Z"], fetch=lambda *a: {}) == ([], "")
