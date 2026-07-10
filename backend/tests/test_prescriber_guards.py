# backend/tests/test_prescriber_guards.py
"""Gardes déterministes prescripteurs (A1, T3). Grounded sur les 4 hors_cible de
la sonde : endora (coach/cours), habiteretgrandir (coach), atelierlesimple
(menuiserie), cotefauteuils (tapissier)."""
from datetime import date

from app.ingestion.prescriber_guards import (
    guard_prescripteur, _has_formation_cue, _has_artisan_metier,
    _has_archi_title, _is_dead_account,
)

TODAY = date(2026, 7, 10)


def test_formation_coach_is_hors_cible():
    # habiteretgrandir : « coach HOMER® » même avec titre archi -> hors_cible.
    prof = {"biography": "Architecte d'intérieur HOMER® / +400 plans en tant que coach HOMER®",
            "postsCount": 447, "followersCount": 743}
    assert _has_formation_cue(prof)
    assert guard_prescripteur(prof, TODAY) == "hors_cible"


def test_cours_prives_is_hors_cible():
    # endora.studio3d : vend des cours privés SketchUp AUX archis (B2B2B).
    prof = {"biography": "Collaboration & Cours privés SketchUp. 3D pour les architectes d'intérieur",
            "postsCount": 49, "followersCount": 187}
    assert guard_prescripteur(prof, TODAY) == "hors_cible"


def test_artisan_without_archi_title_is_hors_cible():
    # atelierlesimple : menuiserie/ébénisterie, PAS d'architecte -> hors_cible.
    prof = {"biography": "Menuiserie & Ébénisterie depuis 1892. Atelier à Charly (18)",
            "fullName": "Menuiserie Atelier Lesimple", "postsCount": 72, "followersCount": 335}
    assert _has_artisan_metier(prof) and not _has_archi_title(prof)
    assert guard_prescripteur(prof, TODAY) == "hors_cible"


def test_tapissier_is_hors_cible():
    prof = {"biography": "Artisan Tapissier Décorateur. Réfections Fauteuils",
            "fullName": "Côté Fauteuils", "postsCount": 30, "followersCount": 200}
    assert guard_prescripteur(prof, TODAY) == "hors_cible"


def test_artisan_WITH_archi_title_passes_to_judge():
    # Un studio archi qui mentionne « menuiserie sur-mesure » NE doit PAS être
    # écarté (titre archi présent) -> None (juge).
    prof = {"fullName": "Juliette de Poncins, architecte d'intérieur",
            "biography": "Interior designer based in Paris. Menuiserie sur-mesure.",
            "postsCount": 132, "followersCount": 681}
    assert _has_archi_title(prof)
    assert guard_prescripteur(prof, TODAY) is None


def test_studio_actif_is_NEVER_deterministic():
    # Titre archi + portfolio : la sonde impose de NE PAS trancher au garde
    # (divnaanni a le titre mais est compte_perso). -> None (juge décide).
    prof = {"fullName": "Atelier du Large", "biography": "Architectures & Intérieurs. Nous concevons des lieux justes.",
            "postsCount": 40, "followersCount": 500}
    assert guard_prescripteur(prof, TODAY) is None


def test_non_prescriber_photographer_is_hors_cible():
    prof = {"biography": "Photographe culinaire, création de contenu pour restaurants",
            "postsCount": 100, "followersCount": 2000}
    assert guard_prescripteur(prof, TODAY) == "hors_cible"


def test_foreign_domain_is_hors_cible():
    prof = {"biography": "Architecte d'intérieur à Bruxelles", "externalUrl": "https://studio.be",
            "postsCount": 50, "followersCount": 300}
    assert guard_prescripteur(prof, TODAY) == "hors_cible"


def test_dead_account_is_noise():
    prof = {"biography": "", "postsCount": 1, "followersCount": 3}
    assert _is_dead_account(prof)
    assert guard_prescripteur(prof, TODAY) == "noise"
