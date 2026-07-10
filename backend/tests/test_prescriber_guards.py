# backend/tests/test_prescriber_guards.py
"""Gardes déterministes prescripteurs (A1, T3). Grounded sur les 4 hors_cible de
la sonde : endora (coach/cours), habiteretgrandir (coach), atelierlesimple
(menuiserie), cotefauteuils (tapissier)."""
import json
from datetime import date
from pathlib import Path

import pytest

from app.ingestion.prescriber_guards import (
    guard_prescripteur, _has_formation_cue, _has_artisan_metier,
    _has_archi_title, _is_dead_account, _furniture_store_identity,
)

TODAY = date(2026, 7, 10)

_SNAP = Path(__file__).resolve().parents[1] / "app" / "ingestion" / "eval" / "snapshots_architectes"


def _snap(handle: str) -> dict:
    p = _SNAP / f"{handle}.json"
    if not p.exists():
        pytest.skip(f"snapshot {handle} absent")
    return json.loads(p.read_text(encoding="utf-8"))


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


def test_foreign_domain_in_externalUrls_list_is_hors_cible():
    # Le ccTLD peut arriver via la liste externalUrls (pas juste externalUrl).
    prof = {"biography": "Architecte d'intérieur à Genève", "externalUrl": "",
            "externalUrls": [{"url": "http://atelier.ch:8080/portfolio"}],
            "postsCount": 50, "followersCount": 300}
    assert guard_prescripteur(prof, TODAY) == "hors_cible"


def test_french_studio_with_cctld_substring_in_host_passes_to_judge():
    # Régression : substring naïf écartait « caroline-studio.fr » (.ca),
    # « benjamin….fr » (.be), « behance.net » (.be), « canva.com » (.ca).
    # Le ccTLD réel étant .fr/.net/.com, ces studios FR doivent aller au juge.
    for url in ("https://www.caroline-studio.fr/",
                "https://www.benjamin-archi.fr",
                "https://www.chloe-interieur.fr",
                "https://www.lucie-design.fr",
                "https://www.behance.net/monstudio",
                "https://www.canva.com/monbook"):
        prof = {"fullName": "Studio archi FR", "biography": "Architecte d'intérieur à Paris",
                "externalUrl": url, "postsCount": 120, "followersCount": 900}
        assert guard_prescripteur(prof, TODAY) is None, url


def test_dead_account_is_noise():
    prof = {"biography": "", "postsCount": 1, "followersCount": 3}
    assert _is_dead_account(prof)
    assert guard_prescripteur(prof, TODAY) == "noise"


def test_stylized_unicode_artisan_bio_is_hors_cible():
    # jks_ebenistes (annotation navigateur, T6) : bio en lettres stylisées
    # Unicode (mathématiques italiques) « É𝘣é𝘯𝘪𝘴𝘵𝘦𝘳𝘪𝘦 » -> doit normaliser en
    # « ebenisterie » (NFKC AVANT le strip d'accents NFD) pour matcher le garde
    # artisan, sans quoi le compte échappe au garde et atterrit à tort chez le
    # juge (constaté : classé studio_actif en run live).
    prof = {"biography": "É𝘣é𝘯𝘪𝘴𝘵𝘦𝘳𝘪𝘦 | 𝘈𝘵𝘦𝘭𝘪𝘦𝘳 | 𝘔𝘰𝘣𝘪𝘭𝘪𝘦𝘳 | 𝘈𝘨𝘦𝘯𝘤𝘦𝘮𝘦𝘯𝘵𝘴 | 𝘚𝘶𝘳-𝘮𝘦𝘴𝘶𝘳𝘦 |",
            "fullName": "Jks Ébénistes", "postsCount": 95, "followersCount": 536}
    assert _has_artisan_metier(prof) and not _has_archi_title(prof)
    assert guard_prescripteur(prof, TODAY) == "hors_cible"


def test_manufacturer_fabricant_without_archi_title_is_hors_cible():
    # schmidt_cambrai (annotation navigateur, T6) : franchise de fabricant
    # (« 1er fabricant français ») sans titre archi -> hors_cible déterministe.
    # Constaté en run live : classé studio_actif, parfois même T2 (hospitality_proof)
    # -> violation du gate « 0 hors_cible en T1/T2 » (non-déterminisme LLM à temp 0).
    prof = {"biography": "Spécialiste de l'aménagement sur mesure. Votre projet, notre "
                          "objectif. 1er fabricant français.",
            "fullName": "Schmidt Cambrai", "postsCount": 483, "followersCount": 522}
    assert _has_artisan_metier(prof) and not _has_archi_title(prof)
    assert guard_prescripteur(prof, TODAY) == "hors_cible"


# --- FRONTIÈRE PRESCRIPTEUR/EXÉCUTANT (precision-archi-1) : 5 faux positifs mesurés
#     doivent tomber au garde ; les 5 vrais + zelee doivent le PASSER (-> juge). ---

@pytest.mark.parametrize("handle", [
    "agenceurmenuisier.fr",   # menuisier-agenceur (handle) MALGRÉ « Architecte d'Intérieur » en bio
    "sartorius_mobilier",     # fabricant de mobilier (nom/handle « mobilier »)
    "rekto_agencement",       # cuisiniste-poseur (bio « Cuisine - Salle de bain - Dressing »)
    "pensart.bzh",            # carreleur / béton ciré
    "lys_brocque_archi_immo", # mandataire immobilier
])
def test_five_measured_false_positives_are_hors_cible_at_guard(handle):
    assert guard_prescripteur(_snap(handle), TODAY) == "hors_cible"


def test_menuisier_in_handle_beats_archi_title_in_bio():
    # agenceurmenuisier.fr : la bio DIT « Architecte d'Intérieur » mais le handle
    # trahit un menuisier -> le garde DUR d'identité écarte MALGRÉ le titre.
    prof = _snap("agenceurmenuisier.fr")
    assert _has_archi_title(prof)  # le titre EST là...
    assert guard_prescripteur(prof, TODAY) == "hors_cible"  # ...mais ne rachète pas


@pytest.mark.parametrize("handle", [
    "zelee_design_studio",          # concept store + archi : fabrication SOUS-TRAITÉE -> juge
    "constantinspire",
    "relionconception",
    "soa.interieur",
    "addiction_design_decoration",
])
def test_five_true_studios_pass_guard_to_judge(handle):
    # AUCUN de ces vrais ne doit être écarté par le garde (verdict None = va au juge).
    assert guard_prescripteur(_snap(handle), TODAY) is None


def test_espacesprojets_mobilier_in_bio_only_passes_to_judge():
    # Garde-fou anti-régression : « mobilier » en BIO (service listé) ne suffit PAS ;
    # seul « mobilier » dans le NOM/handle (marque) écarte. espacesprojets = studio_actif.
    assert guard_prescripteur(_snap("espacesprojets"), TODAY) is None


# --- FRONTIÈRE DESIGN-BUILD (precision-archi-2) : le commerce d'ameublement
#     auto-déclaré est écarté MÊME avec titre archi ; zelee (concept store, pas
#     « magasin d'ameublement ») reste au juge. ---

def test_furniture_store_design_build_beats_archi_title():
    # bontemps.esquisse : bio « Designer & architecte d'intérieur / Magasin
    # d'ameublement et décoration ». Le titre archi EST là mais ne rachète pas un
    # magasin d'ameublement (design-build qui vend/fabrique ce qu'il pose).
    prof = _snap("bontemps.esquisse")
    assert _has_archi_title(prof)             # titre présent...
    assert _furniture_store_identity(prof)    # ...mais commerce d'ameublement déclaré
    assert guard_prescripteur(prof, TODAY) == "hors_cible"


def test_jks_ebenistes_design_build_is_hors_cible():
    # jks_ebenistes (2e cas design-build) : ébéniste-fabricant -> hors_cible au garde.
    assert guard_prescripteur(_snap("jks_ebenistes"), TODAY) == "hors_cible"


def test_zelee_concept_store_not_over_blocked():
    # ANTI-SUR-BLOCAGE : zelee_design_studio est un « Boutique Concept Store &
    # architecte d'intérieur » qui SOUS-TRAITE la fabrication (Atelier Franchini).
    # Il ne se déclare PAS « magasin/boutique d'ameublement/de meubles/de
    # décoration » -> la garde design-build l'ÉPARGNE, il descend au juge.
    prof = _snap("zelee_design_studio")
    assert not _furniture_store_identity(prof)
    assert guard_prescripteur(prof, TODAY) is None


def test_furniture_store_keywords_are_contiguous_phrases_only():
    # « décoration » seul (décoratrice prescriptrice) NE déclenche PAS la garde ;
    # seule la phrase contiguë « magasin/boutique de décoration » le fait.
    assert not _furniture_store_identity(
        {"biography": "Architecte d'intérieur & décoration sur-mesure à Lyon"})
    assert _furniture_store_identity(
        {"biography": "Architecte d'intérieur — Magasin de meubles et boutique de décoration"})
