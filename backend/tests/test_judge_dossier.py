# backend/tests/test_judge_dossier.py
"""Tests du juge v2 unitaire judge_dossier (brique 3)."""
from datetime import date

from app.ingestion.instagram import judge_dossier
from app.ingestion.enrichment.siret_matcher import MatchResult

TODAY = date(2026, 7, 6)


class _FakeCompletion:
    def __init__(self, content):
        self.choices = [type("C", (), {"message": type("M", (), {"content": content})()})()]


class _FakeClient:
    """Client OpenAI factice : renvoie un JSON fixe et capture le prompt."""
    def __init__(self, content):
        self._content = content
        self.last_messages = None
        outer = self

        class _Completions:
            def create(self, **kwargs):
                outer.last_messages = kwargs.get("messages")
                return _FakeCompletion(outer._content)

        self.chat = type("Chat", (), {"completions": _Completions()})()


PROFILE = {
    "postsCount": 3, "followersCount": 120, "businessCategoryName": "Restaurant",
    "biography": "Ouverture prochainement Printemps/Été 2026",
    "latestPosts": [
        {"timestamp": "2026-06-20T10:00:00.000Z", "caption": "Les travaux avancent !"},
        {"timestamp": "2026-05-01T10:00:00.000Z", "caption": "Bientôt chez vous"},
    ],
}


def test_judge_dossier_returns_parsed_label():
    client = _FakeClient('{"reasoning": "pré-ouverture", "label": "opening_soon", '
                         '"confidence": "haute", "addresses": [], "emails": [], '
                         '"opening_date": "2026-04-01"}')
    out = judge_dossier(client, "loumas", "Lou Mas", PROFILE, today=TODAY)
    assert out["label"] == "opening_soon" and out["confidence"] == "haute"


def test_prompt_has_date_anchor_and_reasoning_and_precomputed_ages():
    client = _FakeClient('{"reasoning": "x", "label": "unknown", "confidence": "basse", '
                         '"addresses": [], "emails": [], "opening_date": null}')
    match = MatchResult(siren="9", siret="9", naf="56.10A", enseigne="OCOIN",
                        confidence="moyenne", method="arbitre", date_creation="2026-06-01")
    judge_dossier(client, "x", "X", PROFILE, caption="on ouvre bientôt",
                  match_result=match, today=TODAY)
    joined = " ".join(m["content"] for m in client.last_messages)
    # Ancre de date du jour.
    assert "Date du jour : 2026-07-06" in joined
    # reasoning exigé AVANT le label (fiabilise gpt-4o-mini).
    assert '"reasoning"' in joined
    # Âges PRÉCALCULÉS en code (jamais de timestamp brut à soustraire par le LLM).
    assert "il y a 1 mois" in joined          # société créée (match)
    assert "2026-06-20" not in joined          # timestamp brut du post absent
    assert ("il y a" in joined or "ce mois-ci" in joined)  # âge des posts


def test_judge_dossier_fail_soft():
    assert judge_dossier(None, "x", "X", PROFILE, today=TODAY) == {}
    assert judge_dossier(_FakeClient("pas du json"), "x", "X", PROFILE, today=TODAY) == {}
