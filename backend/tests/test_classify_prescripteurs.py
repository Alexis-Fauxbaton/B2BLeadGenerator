# backend/tests/test_classify_prescripteurs.py
"""classify_prescripteurs : garde -> matcher -> juge -> tiering (A1, T3). Sans réseau."""
from datetime import date

from app.ingestion.instagram import classify_prescripteurs

TODAY = date(2026, 7, 10)


class _FakeCompletion:
    def __init__(self, content):
        self.choices = [type("C", (), {"message": type("M", (), {"content": content})()})()]


class _FakeClient:
    def __init__(self, content):
        class _Completions:
            def create(_self, **kwargs):
                return _FakeCompletion(content)
        self.chat = type("Chat", (), {"completions": _Completions()})()


ACTIF = ('{"reasoning": "x", "label": "studio_actif", "confidence": "haute", '
         '"hospitality_proof": %s, "hospitality_evidence": %s, "addresses": [], "emails": []}')


def _cand(handle, name="Studio"):
    return {"handle": handle, "name": name, "city": "Paris",
            "type": "architecte d'intérieur", "caption": "", "population": "architecte"}


def test_guard_hors_cible_short_circuits_without_llm():
    prof = {"biography": "Menuiserie & Ébénisterie", "fullName": "Menuiserie",
            "postsCount": 72, "followersCount": 335}
    out = classify_prescripteurs([_cand("menuis")], {"menuis": prof},
                                 client=None, match_fn=None, today=TODAY)
    assert out[0]["label"] == "hors_cible" and out[0]["tier"] is None


def test_fail_soft_keeps_as_studio_actif_basse():
    prof = {"biography": "Architecte d'intérieur", "postsCount": 40, "followersCount": 300,
            "latestPosts": [{"timestamp": "2026-07-01T10:00:00.000Z", "caption": "Projet"}]}
    out = classify_prescripteurs([_cand("archi")], {"archi": prof},
                                 client=None, match_fn=None, today=TODAY)
    assert out[0]["label"] == "studio_actif" and out[0]["confidence"] == "basse"
    assert out[0]["tier"] == "T3"  # actif sans preuve hospitality -> T3


def test_tier_t2_when_hospitality_proof_with_evidence():
    # T2 exige hospitality_proof=true ET un extrait cité (hospitality_evidence).
    prof = {"biography": "Architecte d'intérieur", "postsCount": 40, "followersCount": 300,
            "latestPosts": [{"timestamp": "2026-07-01T10:00:00.000Z", "caption": "Hôtel"}]}
    client = _FakeClient(ACTIF % ("true", '"Aménagement complet de l\'hôtel Le Roch"'))
    out = classify_prescripteurs([_cand("archi")], {"archi": prof},
                                 client=client, match_fn=None, today=TODAY)
    assert out[0]["label"] == "studio_actif" and out[0]["tier"] == "T2"
    assert out[0]["hospitality_proof"] is True


def test_hospitality_true_without_evidence_is_refused():
    # Fix 3 (tag T2 fabriqué de rekto) : hospitality_proof=true SANS extrait cité
    # est REFUSÉ par le code -> pas de T2, retombe en T3.
    prof = {"biography": "Architecte d'intérieur", "postsCount": 40, "followersCount": 300,
            "latestPosts": [{"timestamp": "2026-07-01T10:00:00.000Z", "caption": "Projet"}]}
    client = _FakeClient(ACTIF % ("true", '""'))
    out = classify_prescripteurs([_cand("archi")], {"archi": prof},
                                 client=client, match_fn=None, today=TODAY)
    assert out[0]["hospitality_proof"] is False and out[0]["tier"] == "T3"


def test_tier_t1_when_tagged_on_detected_chr_project():
    prof = {"biography": "Architecte d'intérieur", "postsCount": 40, "followersCount": 300,
            "latestPosts": [{"timestamp": "2026-07-01T10:00:00.000Z", "caption": "Projet"}]}
    client = _FakeClient(ACTIF % ("false", '""'))
    out = classify_prescripteurs([_cand("atelierdularge")], {"atelierdularge": prof},
                                 client=client, match_fn=None,
                                 tagged_studios={"atelierdularge"}, today=TODAY)
    assert out[0]["tier"] == "T1"  # T1 domine T2/T3


def test_dormant_has_no_tier():
    prof = {"biography": "Architecte d'intérieur", "postsCount": 447, "followersCount": 16000,
            "latestPosts": [{"timestamp": "2025-10-25T10:00:00.000Z", "caption": "..."}]}
    client = _FakeClient('{"reasoning":"vieux","label":"studio_dormant","confidence":"moyenne",'
                         '"hospitality_proof":false,"addresses":[],"emails":[]}')
    out = classify_prescripteurs([_cand("dormant")], {"dormant": prof},
                                 client=client, match_fn=None, today=TODAY)
    assert out[0]["label"] == "studio_dormant" and out[0]["tier"] is None
