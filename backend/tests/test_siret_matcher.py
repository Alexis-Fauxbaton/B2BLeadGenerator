"""Tests du matcher Insta -> SIREN/SIRET (cas réels des snapshots d'éval)."""
from app.ingestion.enrichment.siret_matcher import (
    clean_name,
    street_number,
    _name_overlap,
    geocode,
    near_candidates,
    pick_by_address,
    arbitrate,
    match,
)
import app.ingestion.enrichment.siret_matcher as sm


def test_clean_name_strips_emojis_and_decorations():
    assert clean_name("MOKA ☕️ Coffee shop & Matcha Bar 🍵") == "MOKA Coffee shop & Matcha Bar"
    # 𝐺𝑖𝑜𝑟𝑔𝑖𝑛𝑎 en "mathematical alphanumeric symbols" -> NFKC -> Giorgina
    assert clean_name("\U0001d43a\U0001d456\U0001d45c\U0001d45f\U0001d454\U0001d456\U0001d45b\U0001d44e 💙") == "Giorgina"


def test_clean_name_keeps_first_segment_before_separators():
    assert clean_name("LE MOURE ROUGE - CANNES 🛟") == "LE MOURE ROUGE"
    assert clean_name("VILLA HENRIETTE • CABOURG") == "VILLA HENRIETTE"
    assert clean_name("Brasserie de la Fontaine • Lourmarin") == "Brasserie de la Fontaine"
    assert clean_name("l'Artémise-Salon de thé") == "l'Artémise"


def test_clean_name_handles_empty():
    assert clean_name(None) == ""
    assert clean_name("🍕🍕") == ""


def test_street_number():
    assert street_number("143  Av. du Général de Gaule Sartrouville") == "143"
    assert street_number("11 rue du Colisée, 75008, Paris") == "11"
    assert street_number("Place de la Fontaine, Lourmarin") is None
    assert street_number(None) is None


def test_name_overlap_uses_distinctive_tokens():
    # 'restaurant'/'le'/'la' sont génériques : pas de match dessus.
    assert _name_overlap("Tre Gusto", "SAR FOOD") is False
    assert _name_overlap("LE MOURE ROUGE", "LE MOURE ROUGE 56.10A CANNES") is True
    assert _name_overlap("LE MOURE ROUGE", "COMMUNE DE CANNES MAIRIE") is False
    assert _name_overlap("CHÈRES COUSINES", "CC ROQUETTE (CHERES COUSINES)") is True


from app.ingestion.enrichment.siret_matcher import _candidates, pick_by_name, _result

# Extraits réels de l'API recherche-entreprises (test du 2026-07-04).
HIT_MOURE = {
    "siren": "899355770", "nom_complet": "LE MOURE ROUGE",
    "activite_principale": "56.10A", "date_creation": "2021-05-17",
    "siege": {"siret": "89935577000012", "activite_principale": "56.10A",
              "adresse": "62 BOULEVARD DE LA CROISETTE 06400 CANNES",
              "code_postal": "06400", "liste_enseignes": None},
}
HIT_MAIRIE = {
    "siren": "210600292", "nom_complet": "COMMUNE DE CANNES",
    "activite_principale": "84.11Z", "date_creation": "1901-01-01",
    "siege": {"siret": "21060029200010", "activite_principale": "84.11Z",
              "adresse": "PL DE L HOTEL DE VILLE 06150 CANNES",
              "code_postal": "06150", "liste_enseignes": ["MAIRIE"]},
}
HIT_AUREA = {
    "siren": "105726145", "nom_complet": "AUREA",
    "activite_principale": "56.10A", "date_creation": "2026-05-28",
    "siege": {"siret": "10572614500014", "activite_principale": "56.10A",
              "adresse": "8 RUE DU LANGUEDOC 06590 THEOULE-SUR-MER",
              "code_postal": "06590", "liste_enseignes": None},
}
HIT_COUSINES = {
    "siren": "994929917", "nom_complet": "CC ROQUETTE (CHERES COUSINES)",
    "activite_principale": "56.10C", "date_creation": "2025-12-15",
    "siege": {"siret": "99492991700017", "activite_principale": "56.10C",
              "adresse": "15 RUE DE LA ROQUETTE 75011 PARIS",
              "code_postal": "75011", "liste_enseignes": ["CHERES COUSINES"]},
}
# Variante near_point : l'établissement matché est dans matching_etablissements.
HIT_OCOIN = {
    "siren": "989119201", "nom_complet": "OCOIN",
    "date_creation": "2025-01-15",
    "matching_etablissements": [{
        "siret": "98911920100011", "activite_principale": "56.10C",
        "adresse": "143 AVENUE GENERAL DE GAULLE 78500 SARTROUVILLE",
        "code_postal": "78500", "liste_enseignes": None,
        "date_creation": "2025-07-04",
    }],
}


def test_candidates_normalizes_siege_and_matching_etablissements():
    cands = _candidates([HIT_MOURE, HIT_OCOIN])
    assert cands[0]["siren"] == "899355770"
    assert cands[0]["naf"] == "56.10A"
    assert cands[0]["adresse"] == "62 BOULEVARD DE LA CROISETTE 06400 CANNES"
    # near_point : l'étage établissement prime sur le siège.
    assert cands[1]["siret"] == "98911920100011"
    assert cands[1]["naf"] == "56.10C"


def test_pick_by_name_accepts_with_geo_consistency():
    cands = _candidates([HIT_MAIRIE, HIT_MOURE])
    got = pick_by_name(cands, "LE MOURE ROUGE", city="Cannes", postal=None)
    # La mairie (NAF non-CHR, pas d'overlap distinctif) est ignorée.
    assert got is not None and got["siren"] == "899355770"


def test_pick_by_name_geo_works_for_paris():
    # 'paris' est dans _GENERIC (noms) mais NE DOIT PAS etre filtre comme VILLE.
    hit = {"siren": "994929917", "nom_complet": "CC ROQUETTE (CHERES COUSINES)",
           "activite_principale": "56.10C", "date_creation": "2025-12-15",
           "siege": {"siret": "99492991700017", "activite_principale": "56.10C",
                     "adresse": "15 RUE DE LA ROQUETTE 75011 PARIS",
                     "code_postal": "75011", "liste_enseignes": ["CHERES COUSINES"]}}
    got = pick_by_name(_candidates([hit]), "CHÈRES COUSINES", city="Paris", postal=None)
    assert got is not None and got["siren"] == "994929917"


def test_pick_by_name_refuses_without_geo():
    # Piège Auréa : nom+NAF collent mais aucune géo connue -> PAS d'auto-accept
    # (ira à l'arbitre). Le backfill actuel aurait mergé à tort.
    cands = _candidates([HIT_AUREA])
    assert pick_by_name(cands, "AURÉA", city=None, postal=None) is None


def test_pick_by_name_refuses_geo_mismatch():
    cands = _candidates([HIT_AUREA])
    assert pick_by_name(cands, "AURÉA", city="Lisbonne", postal=None) is None


def test_http_get_fails_soft(monkeypatch):
    import app.ingestion.enrichment.siret_matcher as sm

    def boom(*a, **k):
        raise OSError("réseau HS")

    monkeypatch.setattr(sm.requests, "get", boom)
    assert sm._http_get(sm.SEARCH_URL, {"q": "x"}) == {}


HIT_SARFOOD = {
    "siren": "948225982", "nom_complet": "SAR FOOD",
    "matching_etablissements": [{
        "siret": "94822598200014", "activite_principale": "56.10C",
        "adresse": "143 AVENUE GENERAL DE GAULLE 78500 SARTROUVILLE",
        "code_postal": "78500", "liste_enseignes": None,
        "date_creation": "2023-03-24",
    }],
}
HIT_CAFETERIA = {
    "siren": "427984489", "nom_complet": "ASS CAFETERIA DES PTT",
    "matching_etablissements": [{
        "siret": "42798448900011", "activite_principale": "56.10A",
        "adresse": "145 AVENUE GENERAL DE GAULLE 78500 SARTROUVILLE",
        "code_postal": "78500", "liste_enseignes": None,
        "date_creation": "1989-05-31",
    }],
}


def _fake_fetch(responses):
    """Fetch factice : {url: réponse}. Enregistre les params reçus."""
    calls = []

    def fetch(url, params):
        calls.append((url, dict(params)))
        return responses.get(url, {})

    fetch.calls = calls
    return fetch


def test_geocode_returns_coords_above_score_threshold():
    import app.ingestion.enrichment.siret_matcher as sm
    ban = {"features": [{"geometry": {"coordinates": [2.1912, 48.9442]},
                         "properties": {"label": "143 Avenue General de Gaulle 78500 Sartrouville",
                                        "score": 0.7}}]}
    fetch = _fake_fetch({sm.BAN_URL: ban})
    assert geocode("143 Av. du Général de Gaule Sartrouville", fetch) == (48.9442, 2.1912)


def test_geocode_rejects_low_score():
    # Cas l'Artémise : BAN géocode "Avenue d'Alsace" à 0.47 -> il faut refuser
    # (sinon on compare aux mauvais voisins).
    import app.ingestion.enrichment.siret_matcher as sm
    ban = {"features": [{"geometry": {"coordinates": [7.36, 48.08]},
                         "properties": {"label": "Avenue d'Alsace 68000 Colmar",
                                        "score": 0.47}}]}
    fetch = _fake_fetch({sm.BAN_URL: ban})
    assert geocode("10 rue des écoles, 68000, Colmar, Alsace", fetch) is None


def test_pick_by_address_single_chr_at_same_number_is_match():
    cands = _candidates([HIT_CAFETERIA, HIT_OCOIN])
    verdict, chosen = pick_by_address(cands, num="143", name="Tre Gusto")
    assert verdict == "match" and chosen[0]["siren"] == "989119201"


def test_pick_by_address_two_chr_at_same_number_is_ambiguous():
    # Cas Tre Gusto réel : SAR FOOD (2023) et OCOIN (2025) au 143 -> arbitre.
    cands = _candidates([HIT_CAFETERIA, HIT_SARFOOD, HIT_OCOIN])
    verdict, pool = pick_by_address(cands, num="143", name="Tre Gusto")
    assert verdict == "ambiguous" and {c["siren"] for c in pool} == {"948225982", "989119201"}


def test_pick_by_address_no_number_or_no_chr_is_none():
    cands = _candidates([HIT_CAFETERIA])
    assert pick_by_address(cands, num=None, name="X") == ("none", [])
    assert pick_by_address([], num="143", name="X") == ("none", [])


class _FakeCompletion:
    def __init__(self, content):
        self.choices = [type("C", (), {"message": type("M", (), {"content": content})()})()]


class _FakeClient:
    """Client OpenAI factice qui renvoie un JSON fixe et capture le prompt."""
    def __init__(self, content):
        self._content = content
        self.last_messages = None
        outer = self

        class _Completions:
            def create(self, **kwargs):
                outer.last_messages = kwargs.get("messages")
                return _FakeCompletion(outer._content)

        self.chat = type("Chat", (), {"completions": _Completions()})()


def test_age_label_precomputes_relative_ages():
    from datetime import date as _d
    from app.ingestion.enrichment.siret_matcher import _age_label
    today = _d(2026, 7, 5)
    assert _age_label("2025-07-04", today) == "il y a 12 mois"
    assert _age_label("2023-03-24", today) == "il y a 3 ans"
    assert _age_label("2026-06-01", today) == "il y a 1 mois"
    assert _age_label("2026-07-20", today) == "dans le futur"
    assert _age_label("2026-07-01", today) == "ce mois-ci"
    assert _age_label("pas-une-date", today) == "?"
    assert _age_label(None, today) == "?"


def test_arbitrate_returns_chosen_siren():
    cands = _candidates([HIT_SARFOOD, HIT_OCOIN])
    client = _FakeClient('{"match_index": 1}')
    assert arbitrate("Tre Gusto", "resto italien qui démarre", cands, client) == "989119201"
    # Le contexte (bio) doit être dans le prompt : c'est lui qui évite Auréa.
    joined = " ".join(m["content"] for m in client.last_messages)
    assert "resto italien qui démarre" in joined


def test_arbitrate_null_means_no_match():
    cands = _candidates([HIT_AUREA])
    client = _FakeClient('{"match_index": null}')
    assert arbitrate("AURÉA", "bijoux, Portugal", cands, client) is None


def test_arbitrate_fails_soft():
    cands = _candidates([HIT_AUREA])
    assert arbitrate("AURÉA", "bio", cands, client=None) is None
    assert arbitrate("AURÉA", "bio", cands, _FakeClient("pas du json")) is None
    assert arbitrate("AURÉA", "bio", [], _FakeClient('{"match_index": 0}')) is None


_BAN_TREGUSTO = {"features": [{"geometry": {"coordinates": [2.1912, 48.9442]},
                               "properties": {"label": "143 Av 78500 Sartrouville",
                                              "score": 0.7}}]}


def test_match_by_name_with_geo():
    fetch = _fake_fetch({sm.SEARCH_URL: {"results": [HIT_MAIRIE, HIT_MOURE]}})
    got = match("LE MOURE ROUGE - CANNES 🛟", city="Cannes", fetch=fetch)
    assert got is not None
    assert (got.siren, got.method, got.confidence) == ("899355770", "nom", "haute")
    assert got.enseigne == "LE MOURE ROUGE"


def test_match_by_address_via_arbiter():
    # Cas Tre Gusto : nom inconnu au registre, 2 CHR au 143 -> arbitre -> OCOIN.
    fetch = _fake_fetch({
        sm.SEARCH_URL: {"results": []},
        sm.BAN_URL: _BAN_TREGUSTO,
        sm.NEAR_URL: {"results": [HIT_CAFETERIA, HIT_SARFOOD, HIT_OCOIN]},
    })
    got = match("Tre Gusto", city="Sartrouville",
                address="143 Av. du Général de Gaule Sartrouville",
                context="resto italien qui démarre",
                fetch=fetch, llm_client=_FakeClient('{"match_index": 1}'))
    assert got is not None
    assert (got.siren, got.siret, got.method) == ("989119201", "98911920100011", "arbitre")


def test_match_single_chr_at_number_without_llm():
    fetch = _fake_fetch({
        sm.SEARCH_URL: {"results": []},
        sm.BAN_URL: _BAN_TREGUSTO,
        sm.NEAR_URL: {"results": [HIT_CAFETERIA, HIT_OCOIN]},
    })
    got = match("Tre Gusto", address="143 Av. du Général de Gaule",
                fetch=fetch, llm_client=None)
    assert got is not None and (got.method, got.confidence) == ("adresse", "moyenne")


def test_match_name_only_without_geo_needs_arbiter():
    # Piège Auréa : sans LLM -> None (conservateur) ; avec LLM qui rejette -> None.
    fetch = _fake_fetch({sm.SEARCH_URL: {"results": [HIT_AUREA]}})
    assert match("AURÉA", fetch=fetch, llm_client=None) is None
    assert match("AURÉA", context="bijoux, Portugal", fetch=fetch,
                 llm_client=_FakeClient('{"match_index": null}')) is None


def test_match_returns_none_when_nothing():
    fetch = _fake_fetch({})
    assert match("MOKA", city="Paris", fetch=fetch) is None


def test_result_enseigne_prefers_enseignes_over_nom():
    # Teste que _result utilise les enseignes si présentes, sinon le nom.
    # Cas 1 : avec enseignes (HIT_COUSINES)
    cousines_cands = _candidates([HIT_COUSINES])
    result_with_enseignes = _result(cousines_cands[0], "haute", "nom")
    assert result_with_enseignes.enseigne == "CHERES COUSINES"

    # Cas 2 : sans enseignes (HIT_MOURE), fallback au nom
    moure_cands = _candidates([HIT_MOURE])
    result_without_enseignes = _result(moure_cands[0], "haute", "nom")
    assert result_without_enseignes.enseigne == "LE MOURE ROUGE"


def test_result_carries_date_creation():
    # HIT_OCOIN porte date_creation sur l'établissement matché (near_point).
    cand = _candidates([HIT_OCOIN])[0]
    r = _result(cand, "moyenne", "adresse")
    assert r.date_creation == "2025-07-04"
    # HIT_MAIRIE : le siège (etab) n'a pas de date_creation -> fallback au niveau
    # res (registre) via `etab.get('date_creation') or res.get('date_creation')`.
    r2 = _result(_candidates([HIT_MAIRIE])[0], "haute", "nom")
    assert r2.date_creation == "1901-01-01"


def test_pipeline_uses_matcher(monkeypatch):
    """run_instagram doit appeler siret_matcher.match (plus backfill_siren)."""
    import app.ingestion.pipeline as pl
    from app.ingestion.enrichment.siret_matcher import MatchResult

    calls = {}

    def fake_match(name, city=None, postal=None, address=None, context=None, **kw):
        calls["name"] = name
        calls["postal"] = postal
        return MatchResult(siren="989119201", siret="98911920100011",
                           naf="56.10C", enseigne="OCOIN",
                           confidence="moyenne", method="arbitre")

    monkeypatch.setattr(pl, "match_siret", fake_match)
    got = pl._match_lead({"handle": "x", "name": "Tre Gusto", "city": "Sartrouville",
                          "address": "143 Av. du Général de Gaule",
                          "bio_snippet": "resto italien"})
    assert calls["name"] == "Tre Gusto"
    assert calls["postal"] is None
    assert got == {"siren": "989119201", "naf": "56.10C", "enseigne": "OCOIN",
                   "siret": "98911920100011", "method": "arbitre", "confidence": "moyenne"}


def test_match_lead_extracts_postal_from_address(monkeypatch):
    import app.ingestion.pipeline as pl
    calls = {}

    def fake_match(name, city=None, postal=None, address=None, context=None, **kw):
        calls["postal"] = postal
        return None

    monkeypatch.setattr(pl, "match_siret", fake_match)
    assert pl._match_lead({"handle": "x", "name": "Y", "city": "Paris",
                           "address": "15 rue de la Roquette, 75011, Paris"}) == {}
    assert calls["postal"] == "75011"


def test_match_lead_none_is_empty_dict():
    import app.ingestion.pipeline as pl
    assert pl._match_lead({"handle": "x", "name": "", "city": ""}) == {}
