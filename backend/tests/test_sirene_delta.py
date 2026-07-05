"""Tests du connecteur delta-Sirene (INSEE) — brique 2 du pivot."""
from datetime import date

from app.ingestion.insee import build_query, fetch_new_etablissements

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
