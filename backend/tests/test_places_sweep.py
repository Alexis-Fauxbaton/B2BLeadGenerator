"""PlacesArchiConnector (B2, T3). Aucun réseau — api_post injecté, checkpoint temp."""
import pytest

from app.ingestion.enrichment.places import search_places_text
from app.ingestion.places_sweep import PlacesArchiConnector, CityCheckpoint, _hospitality


def _place(pid, name, phone="01 02 03 04 05"):
    return {"id": pid, "displayName": {"text": name}, "formattedAddress": f"{name} 75001 Paris",
            "nationalPhoneNumber": phone, "websiteUri": f"https://{pid}.fr",
            "userRatingCount": 12, "primaryType": "interior_designer"}


@pytest.fixture
def tmp_path_json(tmp_path):
    """Fabrique un chemin .json temporaire unique par appel (checkpoint isolé)."""
    counter = {"n": 0}

    def _factory():
        counter["n"] += 1
        return str(tmp_path / f"checkpoint_{counter['n']}.json")

    return _factory


def test_search_uses_20_and_no_chr_gate(monkeypatch):
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "x")
    seen = {}

    def fake_post(url, headers, json):
        seen.update(json)
        return {"places": [_place("a", "Studio Archi")], "nextPageToken": "T2"}

    places, tok, billed = search_places_text("architecte d'intérieur Paris", api_post=fake_post)
    assert seen["maxResultCount"] == 20 and seen["regionCode"] == "FR"
    assert places[0]["phone"] == "01 02 03 04 05" and tok == "T2"
    assert billed is True


def test_search_no_key_returns_empty(monkeypatch):
    monkeypatch.delenv("GOOGLE_PLACES_API_KEY", raising=False)
    places, tok, billed = search_places_text(
        "architecte d'intérieur Paris", api_post=lambda *a, **k: {})
    assert places == [] and tok is None
    assert billed is False        # pas de clé -> aucun appel tenté, jamais facturé


def test_search_network_exception_not_billed(monkeypatch):
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "x")

    def boom(*a, **k):
        raise RuntimeError("network down")

    places, tok, billed = search_places_text("architecte d'intérieur Paris", api_post=boom)
    assert places == [] and tok is None
    assert billed is False        # exception avalée (fail-soft) -> jamais facturé


def test_budget_hard_stop(monkeypatch, tmp_path_json):
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "x")
    calls = {"n": 0}

    def fake_post(url, headers, json):
        calls["n"] += 1
        return {"places": [_place(f"p{calls['n']}", f"Archi {calls['n']}")], "nextPageToken": None}

    conn = PlacesArchiConnector()
    recs = conn.fetch(cities=100, budget_eur=0.05, max_pages=3, api_post=fake_post,
                       checkpoint=CityCheckpoint(path=tmp_path_json()))
    assert conn.spend_eur <= 0.05 + 1e-9        # budget dur respecté
    assert calls["n"] <= 2                        # coupe vite (0.037/appel)


def test_no_key_does_not_spend_or_advance_checkpoint(monkeypatch, tmp_path_json):
    """Repro de la revue : sans cle, fetch() ne doit ni depenser de budget
    factice ni avancer next_city_index (sinon le checkpoint mensuel serait
    epuise a tort avec zero appel Google reel)."""
    monkeypatch.delenv("GOOGLE_PLACES_API_KEY", raising=False)
    conn = PlacesArchiConnector()
    recs = conn.fetch(cities=1, budget_eur=10.0,
                       checkpoint=CityCheckpoint(path=tmp_path_json()))
    assert recs == []
    assert conn.spend_eur == 0.0
    assert conn.next_city_index == 0


def test_network_exception_does_not_spend(monkeypatch, tmp_path_json):
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "x")

    def boom(url, headers, json):
        raise RuntimeError("network down")

    conn = PlacesArchiConnector()
    recs = conn.fetch(cities=1, budget_eur=10.0, api_post=boom,
                       checkpoint=CityCheckpoint(path=tmp_path_json()))
    assert recs == []
    assert conn.spend_eur == 0.0
    assert conn.next_city_index == 0


@pytest.mark.parametrize("name", ["Chretien Architecture", "Chraibi Design",
                                   "Dechriste Interieurs", "Chretien", "Chraibi", "Dechriste"])
def test_hospitality_no_false_positive_on_surnames(name):
    """Repro de la revue : sans delimitation de mot, le token court "chr" de
    HOSPITALITY_KEYWORDS matchait ces patronymes francais courants en
    sous-chaine et taggait a tort un architecte en tier T2."""
    assert _hospitality(name) is False


def test_hospitality_matches_standalone_chr_token():
    assert _hospitality("CHR Conseil Deco") is True
    assert _hospitality("Studio Hotels & Restaurants") is True


def test_checkpoint_resumes(monkeypatch, tmp_path_json):
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "x")
    cp = CityCheckpoint(path=tmp_path_json())
    cp.save(next_city_index=5, spend_eur=0.0)
    conn = PlacesArchiConnector()

    def fake_post(url, headers, json):
        return {"places": [], "nextPageToken": None}

    conn.fetch(cities=100, budget_eur=10, api_post=fake_post, checkpoint=cp)
    assert conn.next_city_index >= 5             # reprise au bon endroit


def test_to_candidates_hospitality_and_phone_in_raw(monkeypatch):
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "x")
    conn = PlacesArchiConnector()
    cand = conn.to_candidates([{"place_id": "z", "name": "Deco Hotels & Restaurants",
        "formatted": "10 rue X 75002 Paris", "phone": "06 07 08 09 10",
        "website": "https://z.fr", "hospitality": True}])[0]
    assert cand.source == "places" and cand.source_ref == "places:z"
    assert "portfolio hospitality/CHR" in cand.secondary_signals   # tier T2
    assert cand.raw["phone"] == "06 07 08 09 10" and cand.website == "https://z.fr"
