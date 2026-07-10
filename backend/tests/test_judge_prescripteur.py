# backend/tests/test_judge_prescripteur.py
"""Juge prescripteur unitaire (A1, T3) — sans réseau (client factice)."""
from datetime import date

from app.ingestion.instagram import judge_prescripteur

TODAY = date(2026, 7, 10)


class _FakeCompletion:
    def __init__(self, content):
        self.choices = [type("C", (), {"message": type("M", (), {"content": content})()})()]


class _FakeClient:
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
    "postsCount": 132, "followersCount": 681, "businessCategoryName": None,
    "biography": "Interior designer based in Paris",
    "externalUrl": "http://juliettedeponcins.com/",
    "latestPosts": [
        {"timestamp": "2026-07-10T10:00:00.000Z", "caption": "Projet Bargue — banquette sur-mesure"},
        {"timestamp": "2026-07-02T10:00:00.000Z", "caption": "Atmosphère chaleureuse"},
    ],
}


def test_returns_parsed_label_and_hospitality():
    client = _FakeClient('{"reasoning": "portfolio actif", "label": "studio_actif", '
                         '"confidence": "haute", "hospitality_proof": true, '
                         '"addresses": [], "emails": ["contact@jdp.com"]}')
    out = judge_prescripteur(client, "atelier_jdp", "Juliette", PROFILE, today=TODAY)
    assert out["label"] == "studio_actif"
    assert out["hospitality_proof"] is True
    assert out["emails"] == ["contact@jdp.com"]


def test_prompt_has_date_anchor_precomputed_recency_and_reasoning():
    client = _FakeClient('{"reasoning": "x", "label": "studio_dormant", "confidence": "moyenne", '
                         '"hospitality_proof": false, "addresses": [], "emails": []}')
    judge_prescripteur(client, "x", "X", PROFILE, today=TODAY)
    joined = " ".join(m["content"] for m in client.last_messages)
    assert "Date du jour : 2026-07-10" in joined
    assert '"reasoning"' in joined                 # reasoning exigé avant le label
    assert "dernier post" in joined.lower()         # récence précalculée présente
    assert "2026-07-10T10" not in joined            # timestamp brut jamais donné au LLM
    # Espace de labels prescripteurs présent dans le format de sortie.
    assert "studio_actif" in joined and "compte_perso" in joined and "hors_cible" in joined


def test_fail_soft():
    assert judge_prescripteur(None, "x", "X", PROFILE, today=TODAY) == {}
    assert judge_prescripteur(_FakeClient("pas du json"), "x", "X", PROFILE, today=TODAY) == {}
