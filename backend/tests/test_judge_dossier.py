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
    """Client OpenAI factice : renvoie un JSON fixe et capture le prompt + kwargs."""
    def __init__(self, content):
        self._content = content
        self.last_messages = None
        self.last_kwargs = None
        outer = self

        class _Completions:
            def create(self, **kwargs):
                outer.last_messages = kwargs.get("messages")
                outer.last_kwargs = kwargs
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


def test_judge_uses_dedicated_model_defaulting_to_gpt4o(monkeypatch):
    """Passe 3 (décision 1) : le juge tourne sur un modèle FORT dédié —
    OPENAI_JUDGE_MODEL, défaut « gpt-4o » quand la variable est absente. Il ne doit
    PAS retomber sur OPENAI_MODEL (réservé au reste : arbitre matcher, messages…).
    gpt-4o-mini est non déterministe à temp 0 sur les profils ambigus (vécu passe 3)."""
    monkeypatch.delenv("OPENAI_JUDGE_MODEL", raising=False)
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-mini")  # ne doit PAS être utilisé
    client = _FakeClient('{"reasoning":"x","label":"unknown","confidence":"basse",'
                         '"addresses":[],"emails":[],"opening_date":null}')
    judge_dossier(client, "x", "X", PROFILE, today=TODAY)
    assert client.last_kwargs["model"] == "gpt-4o"
    # La variable dédiée, si présente, l'emporte.
    monkeypatch.setenv("OPENAI_JUDGE_MODEL", "gpt-4o-2024-11-20")
    judge_dossier(client, "x", "X", PROFILE, today=TODAY)
    assert client.last_kwargs["model"] == "gpt-4o-2024-11-20"


def test_judge_dossier_fail_soft():
    assert judge_dossier(None, "x", "X", PROFILE, today=TODAY) == {}
    assert judge_dossier(_FakeClient("pas du json"), "x", "X", PROFILE, today=TODAY) == {}


def test_judge_prompt_mentions_second_address_chain():
    from app.ingestion.instagram import _DOSSIER_SYSTEM
    t = _DOSSIER_SYSTEM.lower()
    # La règle chaîne cible explicitement la 2e adresse d'une marque existante.
    assert "adresse" in t and ("2e adresse" in t or "nouvelle adresse" in t or "2e établissement" in t)


def test_judge_prompt_covers_new_account_not_new_venue():
    """Règle restaurée (piège compte-neuf) : un compte RÉCENT ne fait pas un
    établissement récent — 'ouverts depuis <année>' = established quel que soit
    l'âge du compte (perdu dans une réécriture, cause du faux opening_soon prod)."""
    from app.ingestion.instagram import _DOSSIER_SYSTEM
    t = _DOSSIER_SYSTEM.lower()
    assert "compte récent" in t
    assert "ouverts depuis" in t
    assert "depuis 19xx/20xx" in t
    assert "established" in t


def test_judge_prompt_defines_renovation_label_and_rule():
    """Passe 3 : le label renovation (établi EN TRAVAUX = segment chaud) et sa
    règle de datation doivent être présents dans le system prompt, ET dans l'enum
    du format JSON attendu."""
    from app.ingestion.instagram import _DOSSIER_SYSTEM
    t = _DOSSIER_SYSTEM.lower()
    # Label défini + segment chaud.
    assert "renovation" in t
    assert "travaux" in t
    # Règle de datation : travaux EN COURS / réouverture récente -> renovation,
    # réouverture plus ancienne / opère normalement -> established.
    assert "en cours" in t
    assert "established" in t
    # L'enum du format JSON de sortie propose bien renovation (sinon le juge ne
    # peut PAS émettre le label).
    client = _FakeClient('{"reasoning":"x","label":"renovation","confidence":'
                         '"haute","addresses":[],"emails":[],"opening_date":null}')
    out = judge_dossier(client, "x", "X", PROFILE, today=TODAY)
    assert out["label"] == "renovation"
    joined = " ".join(m["content"] for m in client.last_messages)
    assert "renovation" in joined


def test_judge_prompt_hardens_just_opened():
    """Passe 3 (section B) : just_opened exige une preuve EXPLICITE de lancement
    récent ; horaires + historique SANS preuve = established."""
    from app.ingestion.instagram import _DOSSIER_SYSTEM
    t = _DOSSIER_SYSTEM.lower()
    assert "just_opened exige" in t
    assert "preuve explicite" in t


def test_judge_prompt_has_three_hardening_rules():
    """Remédiation 3bis : le juge sur-prédisait opening_soon. Trois règles ajoutées
    au system prompt doivent être présentes (garde le juge honnête)."""
    from app.ingestion.instagram import _DOSSIER_SYSTEM
    t = _DOSSIER_SYSTEM.lower()
    # 1. RÈGLE DE DOUTE : opening_soon exige des indices EXPLICITES.
    assert "explicite" in t
    assert "jamais" in t and "opening_soon" in t
    # 2. NOT_VENUE PRIORITAIRE : trancher d'abord si CHR physique en France.
    assert "not_venue prioritaire" in t or ("d'abord" in t and "not_venue" in t)
    assert "hors france" in t
    # 3. CHAÎNE : nouvelle adresse d'une enseigne existante = chain_multisite,
    #    même si création récente.
    assert "chain_multisite" in t and "récente" in t
