# backend/tests/test_profile_guards.py
"""Tests des garde-fous déterministes du funnel v2 (brique 3)."""
import json
from datetime import date
from pathlib import Path

from app.ingestion.profile_guards import (
    guard_verdict,
    _has_hours_in_bio,
    _has_reservation_link,
    _count_addresses_in_bio,
    _has_reservation_in_bio,
    _has_reservation_in_posts,
    _has_opening_cue,
    _multi_city_in_bio,
    _is_dead_account,
)

SNAP = Path(__file__).resolve().parents[1] / "app" / "ingestion" / "eval" / "snapshots"
TODAY = date(2026, 7, 6)


def test_hours_in_bio_detected():
    assert _has_hours_in_bio("Ouvert du lundi au samedi de 10h à 19h")
    assert _has_hours_in_bio("Open everyday")
    assert _has_hours_in_bio("Tous les jours 10:30-23:00")
    assert _has_hours_in_bio("Service 7j/7 midi et soir")


def test_hours_absent_for_preopening_bios():
    # Les 4 cas 'opening' de la vérité terrain n'ont NI horaires NI résa.
    assert not _has_hours_in_bio("Ouverture prochainement Printemps/Été 2026")
    assert not _has_hours_in_bio("Bientôt chez vous — Juillet 2026")
    assert not _has_hours_in_bio("Resto italien qui démarre, pas encore d'horaires")


def test_reservation_link_detected():
    assert _has_reservation_link({"externalUrl": "https://bookings.zenchef.com/x"})
    assert _has_reservation_link({"externalUrls": [{"url": "https://www.thefork.fr/r/x"}]})
    assert _has_reservation_link({"biography": "Résa: https://lafourchette.com/xyz"})
    assert _has_reservation_link({"externalUrl": "https://resy.com/cities/paris/x"})
    assert not _has_reservation_link({"externalUrl": "https://mon-site-perso.fr"})


def test_count_addresses_in_bio():
    # Liste de lieux marquée par un pin et séparée par | -> multi-sites.
    assert _count_addresses_in_bio("📍Champs Elysées | Opéra | Galeries Lafayette") >= 2
    # Une seule adresse -> 1.
    assert _count_addresses_in_bio("📍44 rue de Ponthieu 75008 Paris") == 1
    # Deux codes postaux distincts -> 2.
    assert _count_addresses_in_bio("12 rue X 75001 et 8 rue Y 75009") >= 2
    assert _count_addresses_in_bio("Café de spécialité, ambiance cosy") == 0


def test_moka_dies_here_as_chain_multisite():
    snap = json.loads((SNAP / "cafe_mokaparis.json").read_text(encoding="utf-8"))
    # 3 adresses en bio + 'open everyday' : doit mourir ICI, sans LLM.
    assert guard_verdict(snap, TODAY) == "chain_multisite"


def test_posts_count_hard_established():
    assert guard_verdict({"postsCount": 200, "biography": ""}, TODAY) == "established"


def test_long_history_established():
    prof = {"postsCount": 11, "biography": "", "latestPosts": [
        {"timestamp": "2023-01-05T10:00:00.000Z"},
        {"timestamp": "2023-06-05T10:00:00.000Z"},
        {"timestamp": "2024-01-05T10:00:00.000Z"},
    ]}
    assert guard_verdict(prof, TODAY) == "established"


def test_hours_bio_established():
    assert guard_verdict({"postsCount": 20, "biography": "Ouvert 7j/7 midi et soir"},
                         TODAY) == "established"


def test_preopening_passes_through_to_llm():
    # Peu de posts, bio de pré-ouverture, aucun signal établi -> None (va au juge).
    prof = {"postsCount": 2,
            "biography": "Ouverture prochainement Printemps/Été 2026",
            "latestPosts": [{"timestamp": "2026-06-20T10:00:00.000Z"}]}
    assert guard_verdict(prof, TODAY) is None


# --- Régression sur snapshots réels (garde-fou vs vérité terrain) --------------
# Les 4 comptes 'opening' NE DOIVENT PAS être tranchés par les gardes : ils
# doivent descendre au juge LLM (verdict déterministe None), sinon le gate dur
# recall_opening == 1.0 (T5) devient inatteignable. Bug attrapé en revue :
# chezgratien ('📍 Villeneuve d'Aveyron | Juillet 2026') sortait chain_multisite.
OPENING_SNAPS = ["loumasrestaurant", "chezgratien_hotelbistrospa",
                 "tregusto_sartrouville", "brasseriedelafontainelourmarin"]


def test_opening_snapshots_pass_through_to_llm():
    for h in OPENING_SNAPS:
        snap = json.loads((SNAP / f"{h}.json").read_text(encoding="utf-8"))
        assert guard_verdict(snap, TODAY) is None, f"{h} tranché à tort par un garde-fou"


def test_chezgratien_pin_date_not_counted_as_address():
    # '📍 Villeneuve d'Aveyron | Juillet 2026' : le 2e segment est une DATE
    # d'ouverture, pas une adresse -> 1 seule adresse, pas de chain_multisite.
    snap = json.loads((SNAP / "chezgratien_hotelbistrospa.json").read_text(encoding="utf-8"))
    assert _count_addresses_in_bio(snap.get("biography") or "") < 2
    assert guard_verdict(snap, TODAY) != "chain_multisite"


def test_hours_guard_ignores_countdown():
    # Un compte à rebours "48h" n'est PAS un horaire d'ouverture...
    assert not _has_hours_in_bio("Ouverture dans 48h !")
    # ...mais une vraie plage horaire-only ("10h-18h") EST détectée.
    assert _has_hours_in_bio("Service 10h-18h")


def test_reservation_in_bio_helper():
    assert _has_reservation_in_bio("Réservation : 01 43 25 87 99")
    assert not _has_reservation_in_bio("Réservez votre table très bientôt")   # pas de tel
    assert not _has_reservation_in_bio("Café de spécialité, 5 rue du Marché")  # pas de résa


def test_multi_city_in_bio_helper():
    assert _multi_city_in_bio("Lyon 6, Paris 11")
    assert _multi_city_in_bio("Bordeaux | Toulouse")
    assert not _multi_city_in_bio("Bagels à Paris 11")                       # 1 ville
    assert not _multi_city_in_bio("Villeneuve d'Aveyron | Juillet 2026")     # 1 ville + date


def test_reservation_in_posts_helper():
    # En service : résa + URL et AUCUN indice d'ouverture -> True.
    assert _has_reservation_in_posts(
        {"latestPosts": [{"caption": "réservations sur notre site internet www.x.fr"}]})
    assert not _has_reservation_in_posts(
        {"latestPosts": [{"caption": "on ouvre bientôt, restez connectés !"}]})
    assert not _has_reservation_in_posts({"latestPosts": []})


def test_reservation_in_posts_ignores_preopening():
    # RÉGRESSION (garde-fou rappel opening) : une pré-ouverture qui tease DÉJÀ la
    # réservation en ligne ne doit JAMAIS être captée comme established -> None ->
    # elle reste au juge. Reproduit le vrai profil villa.henriette_cabourg.
    preopening = {
        "biography": "📅 Ouverture 10 Juillet 2026 ! #openingsoon",
        "latestPosts": [
            {"caption": "Rendez-vous pour les réservations sur www.villa-henriette.fr"},
            {"caption": "OPENING SOON !!! L'ouverture approche"},
        ],
    }
    assert _has_opening_cue(preopening)
    assert not _has_reservation_in_posts(preopening)


def test_osabaita_established_by_guard():
    snap = json.loads((SNAP / "osabaita.json").read_text(encoding="utf-8"))
    # Résa téléphone en bio + fr.newtable.com en externalUrls -> established, sans LLM.
    assert guard_verdict(snap, TODAY) == "established"


def test_villa_henriette_passes_to_judge():
    snap = json.loads((SNAP / "villa.henriette_cabourg.json").read_text(encoding="utf-8"))
    # villa.henriette est une PRÉ-OUVERTURE (bio « Ouverture 10 Juillet 2026 »,
    # « OPENING SOON », ouvre 2 jours après TODAY=2026-07-08) qui tease déjà la
    # résa en ligne. Le garde résa-posts est vetoé par `_has_opening_cue` -> None
    # -> villa retombe au juge (assertion de NON-RÉGRESSION : jamais captée au
    # garde, sinon perte d'un vrai opening_soon). Reste verte de bout en bout.
    assert guard_verdict(snap, TODAY) is None


def test_cherescousines_chain_by_guard():
    snap = json.loads((SNAP / "cherescousinesbagels.json").read_text(encoding="utf-8"))
    # Bio « Lyon 6, Paris 11 » = deux villes = marque multi-sites, sans LLM.
    assert guard_verdict(snap, TODAY) == "chain_multisite"


# --- Garde compte-mort (remédiation 3bis : le juge sur-prédisait opening_soon) --
def test_chickntikka_snapshot_has_opening_cue_so_spared():
    # NB remédiation 2026-07-09 : le snapshot réel de chickntikka94 (2 posts / 1
    # abonné / pas de bio) porte des indices d'ouverture EXPLICITES dans ses
    # légendes (« Il ne manque plus que l'ouverture », « Très bientôt »,
    # #OuvertureProchaine #ComingSoon #NouvelleAdresse). Par le MÊME veto
    # `_has_opening_cue` qui protège loumas/tregusto, il est donc ÉPARGNÉ par la
    # garde compte-mort et retombe au juge — on ne tue jamais un signal
    # d'ouverture, quelle que soit la maigreur du compte (garde-fou absolu).
    snap = json.loads((SNAP / "chickntikka94.json").read_text(encoding="utf-8"))
    assert _has_opening_cue(snap) is True
    assert _is_dead_account(snap) is False
    assert guard_verdict(snap, TODAY) is None


def test_dead_account_noise_on_synthetic_dead_profile():
    # Compte-mort SANS aucun indice d'ouverture -> 'noise' déterministe, sans juge.
    dead = {"postsCount": 2, "followersCount": 1, "biography": "",
            "latestPosts": [{"caption": "🔥🔥🔥"}, {"caption": "😈"}]}
    assert _is_dead_account(dead) is True
    assert guard_verdict(dead, TODAY) == "noise"


def test_dead_account_guard_spares_preopenings():
    # GARDE-FOU rappel opening : les pré-ouvertures naissantes (peu de posts/peu
    # d'abonnés MAIS indice d'ouverture) ne doivent JAMAIS être écrasées en noise.
    # loumasrestaurant (2 posts, bio 'ouverture prochainement') et tregusto
    # (4 posts, captions d'ouverture) + les 4 openings -> None (restent au juge).
    for h in OPENING_SNAPS:
        snap = json.loads((SNAP / f"{h}.json").read_text(encoding="utf-8"))
        assert _is_dead_account(snap) is False, f"{h} écrasé à tort en noise"
        assert guard_verdict(snap, TODAY) is None, f"{h} tranché à tort par un garde-fou"


def test_dead_account_needs_all_conditions():
    # Peu de posts + peu d'abonnés MAIS bio non vide -> PAS un compte-mort.
    assert _is_dead_account(
        {"postsCount": 2, "followersCount": 3, "biography": "Café de spécialité à Paris"}) is False
    # Peu de posts + peu d'abonnés + bio vide MAIS indice d'ouverture -> PAS mort.
    assert _is_dead_account(
        {"postsCount": 1, "followersCount": 2, "biography": "",
         "latestPosts": [{"caption": "On ouvre bientôt !"}]}) is False
    # Trop d'abonnés -> PAS mort.
    assert _is_dead_account({"postsCount": 2, "followersCount": 500, "biography": ""}) is False
    # Compteurs manquants -> PAS mort (fail-soft, on ne devine pas).
    assert _is_dead_account({"biography": ""}) is False
    # Toutes conditions réunies -> mort.
    assert _is_dead_account({"postsCount": 2, "followersCount": 1, "biography": ""}) is True


def test_just_opened_monica_survives_guards():
    # monica_stgermain (just_opened) : peu de posts, pas d'horaires détectés,
    # historique court -> descend au juge (None). NB : imagine.trouville, elle,
    # est captée en 'established' par _long_history (posts de 2024) — perte de
    # rappel just_opened ASSUMÉE et documentée (cf. « Notes de revue »), non
    # fatale au gate d'acceptation (qui ne couvre que 'opening').
    snap = json.loads((SNAP / "monica_stgermain.json").read_text(encoding="utf-8"))
    assert guard_verdict(snap, TODAY) is None
