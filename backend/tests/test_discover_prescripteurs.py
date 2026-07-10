# backend/tests/test_discover_prescripteurs.py
"""Tests de la découverte prescripteurs (A1, T2) — PURE, sans réseau. Grounded sur
les handles réels de la sonde (atelier_jdp, atelierlesimple, endora.studio3d…)."""
from app.ingestion.instagram import discover_prescripteurs


def _post(handle, name="", caption="", hashtags=(), location="Paris, France"):
    return {"ownerUsername": handle, "ownerFullName": name, "caption": caption,
            "hashtags": list(hashtags), "locationName": location}


def test_keeps_interior_architect_by_bio_keyword():
    out = discover_prescripteurs([
        _post("atelier_jdp", "Juliette de Poncins, architecte d'intérieur",
              "Projet Bargue", ("architectedinterieur",), "Paris"),
    ])
    assert len(out) == 1
    c = out[0]
    assert c["handle"] == "atelier_jdp"
    assert c["population"] == "architecte"
    assert c["type"] == "architecte d'intérieur"
    assert c["caption"]  # caption conservée pour le juge


def test_keeps_agencement_even_artisan_discovery_is_broad():
    # atelierlesimple (menuiserie) est capté par #agencement à la DÉCOUVERTE :
    # volontaire (large). Le garde/juge (T3) l'écartera en hors_cible.
    out = discover_prescripteurs([
        _post("atelierlesimple", "Menuiserie Atelier Lesimple",
              "Lambris en chêne", ("agencement",), "Charly"),
    ])
    assert [c["handle"] for c in out] == ["atelierlesimple"]


def test_no_idf_no_chr_filter_national_volume():
    # Compte hors IdF (Pays de la Loire) + AUCUN mot CHR : gardé quand même
    # (national, VOLUME MAX). discover() CHR l'aurait écarté.
    out = discover_prescripteurs([
        _post("espacesprojets", "Atelier Espaces & Projets",
              "Aménagement bureaux sur mesure", ("agencement",), "Château-Gontier"),
    ])
    assert [c["handle"] for c in out] == ["espacesprojets"]
    assert out[0]["city"] == "Château-Gontier"


def test_keeps_compound_hashtag_without_plaintext_phrase():
    # Piège sonde : #architectedinterieur (composé, sans espace) est le tag le plus
    # productif. Un post SANS aucune phrase archi en clair (nom + caption anodins)
    # mais portant ce hashtag doit être retenu — le compte a été découvert PAR ce
    # tag. Les mots-clés à espace seuls le rateraient.
    out = discover_prescripteurs([
        _post("bifur.architecture", "Bifur", "Projet livré",
              ("architectedinterieur",), "Nantes"),
    ])
    assert [c["handle"] for c in out] == ["bifur.architecture"]


def test_drops_unrelated_and_dedupes():
    out = discover_prescripteurs([
        _post("fitcoach", "Coach sportif", "workout", ("fitness",), "Lyon"),
        _post("atelier_jdp", "archi", "1", ("architecturedinterieure",)),
        _post("atelier_jdp", "archi", "2", ("architectedinterieur",)),  # doublon
    ])
    assert [c["handle"] for c in out] == ["atelier_jdp"]  # fitcoach écarté, dédup


def test_empty_handle_skipped():
    out = discover_prescripteurs([_post("", "archi d'intérieur", "x", ("interiordesign",))])
    assert out == []


def test_archi_hashtags_present():
    from app.ingestion.instagram import ARCHI_HASHTAGS
    for h in ("architectedinterieur", "architecturedinterieure", "agencement"):
        assert h in ARCHI_HASHTAGS
