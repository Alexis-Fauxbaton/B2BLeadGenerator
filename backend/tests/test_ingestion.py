"""Tests unitaires de l'ingestion (sans réseau)."""
from app.ingestion.base import LeadCandidate
from app.ingestion.bodacc import BodaccConnector
from app.ingestion.chr_classifier import classify
from app.ingestion.enrichment.naf_classifier import classify_naf
from app.ingestion.enrichment.sirene import apply_sirene_data
from datetime import date


def test_classifier_detects_chr_types():
    assert classify("Restauration traditionnelle") == "restaurant"
    assert classify("Hôtel-restaurant") == "hôtel"
    assert classify("débit de boissons, bar à cocktails") == "bar"
    assert classify("Salon de thé et café") == "café"
    assert classify("BRASSERIE artisanale") == "brasserie"
    assert classify("traiteur événementiel") == "traiteur"


def test_classifier_rejects_non_chr_and_exclusions():
    assert classify("marchand de biens") is None
    assert classify("agence immobilière") is None
    assert classify("restauration collective") is None  # exclusion
    assert classify("") is None


def test_classifier_word_boundary_and_false_positives():
    # "bar" ne doit PAS matcher dans "barbershop" / "barber".
    assert classify("BARBERSHOP CHAMPAGNE") is None
    assert classify("BARBER'R salon de coiffure") is None
    # "restauration d'objets" = ébénisterie, pas un restaurant.
    assert classify("restauration d'objets artisanaux en bois") is None
    # mais un vrai bar reste détecté.
    assert classify("bar à cocktails") == "bar"
    assert classify("Le Petit Bar") == "bar"


def test_bodacc_maps_creation_to_signal():
    record = {
        "id": "A2026TEST01",
        "familleavis": "creation",
        "familleavis_lib": "Création d'établissement",
        "commercant": "Le Bon Bistrot",
        "ville": "Paris",
        "cp": "75011",
        "dateparution": "2026-06-20",
        "url_complete": "https://www.bodacc.fr/annonce/A2026TEST01",
        "listepersonnes": (
            '{"personne": {"typePersonne": "pm", "denomination": "Le Bon Bistrot",'
            ' "activite": "restauration traditionnelle",'
            ' "adresseSiegeSocial": {"numeroVoie": "12", "typeVoie": "rue",'
            ' "nomVoie": "Oberkampf", "codePostal": "75011", "ville": "Paris"}}}'
        ),
        "listeetablissements": None,
        "listeprecedentproprietaire": None,
        "listeprecedentexploitant": None,
        "jugement": None,
        "acte": None,
    }
    candidates = BodaccConnector().to_candidates([record])
    assert len(candidates) == 1
    c = candidates[0]
    assert c.source == "bodacc"
    assert c.source_ref == "A2026TEST01"
    assert c.main_signal == "création récente"
    assert c.establishment_name == "Le Bon Bistrot"
    assert c.city == "Paris"
    assert "Oberkampf" in c.address
    assert "restauration" in c.classification_text.lower()
    assert c.proof_url.endswith("A2026TEST01")
    # Vérifie que le classifier le reconnaît bien comme CHR.
    assert classify(c.classification_text) == "restaurant"


def test_bodacc_creation_with_previous_operator_is_reprise():
    """Une 'création' qui nomme un précédent exploitant/propriétaire est en fait
    une reprise (nouvelle société sur un fonds existant) -> reclassée directement
    par le registre, sans avoir besoin d'un proxy externe (avis Places)."""
    record = {
        "id": "A2026TEST05",
        "familleavis": "creation",
        "commercant": "Le Repris Bistrot",
        "ville": "Paris",
        "cp": "75011",
        "dateparution": "2026-06-20",
        "listepersonnes": '{"personne": {"denomination": "Le Repris Bistrot",'
        ' "activite": "restauration traditionnelle"}}',
        "listeprecedentexploitant": "ANCIEN EXPLOITANT SARL",
        "listeprecedentproprietaire": None,
    }
    c = BodaccConnector().to_candidates([record])[0]
    assert c.main_signal == "reprise"
    assert "changement propriétaire" in c.secondary_signals
    # Sanity : une création SANS précédent reste une création récente.
    record["listeprecedentexploitant"] = None
    c2 = BodaccConnector().to_candidates([record])[0]
    assert c2.main_signal == "création récente"


def test_bodacc_modification_without_owner_change_is_skipped():
    record = {
        "id": "A2026TEST02",
        "familleavis": "modification",
        "commercant": "Restaurant Modifié",
        "ville": "Lyon",
        "cp": "69001",
        "dateparution": "2026-06-20",
        "listepersonnes": '{"personne": {"denomination": "Restaurant Modifié",'
        ' "activite": "restaurant"}}',
        "listeprecedentproprietaire": None,
        "listeprecedentexploitant": None,
    }
    # Modification sans changement de propriétaire -> écartée.
    assert BodaccConnector().to_candidates([record]) == []


def test_bodacc_prefers_trade_name_over_civil_name():
    """Pour une entreprise individuelle, on veut l'enseigne (nomCommercial)
    comme nom d'établissement et le nom civil comme décideur."""
    record = {
        "id": "A2026TEST04",
        "familleavis": "creation",
        "commercant": "Cresson, Sandrine, Corbeaux",  # nom civil (à NE PAS afficher)
        "ville": "Saint-Fargeau-Ponthierry",
        "cp": "77310",
        "dateparution": "2026-06-26",
        "listepersonnes": (
            '{"personne": {"typePersonne": "pp", "nom": "Cresson",'
            ' "prenom": "Sandrine", "nomUsage": "Corbeaux",'
            ' "nomCommercial": "SANDY CHEZ VOUS"}}'
        ),
        "listeetablissements": '{"etablissement": {"activite": "traiteur a domicile"}}',
        "listeprecedentproprietaire": None,
        "listeprecedentexploitant": None,
    }
    c = BodaccConnector().to_candidates([record])[0]
    assert c.establishment_name == "SANDY CHEZ VOUS"
    assert c.decision_maker == "Sandrine Cresson"


def test_places_match_validation():
    from app.ingestion.enrichment.places import _is_chr_type, _location_ok

    # Type : CHR accepté, non-CHR rejeté (garde anti faux-match).
    assert _is_chr_type("french_restaurant") is True
    assert _is_chr_type("bistro") is True
    assert _is_chr_type("hair_salon") is False  # cas SandyPro
    assert _is_chr_type("sports_complex") is False
    # Localisation : postal OU ville.
    assert _location_ok("12 Rue X, 75018 Paris", postal="75010", city="Paris") is True  # ville
    assert _location_ok("12 Rue X, 75018 Paris", postal="75018", city=None) is True  # postal
    assert _location_ok("12 Rue X, 69001 Lyon", postal="75010", city="Paris") is False


def test_places_distance_validation():
    from app.ingestion.enrichment.places import _location_ok_geo, _haversine_m

    # Même point -> distance ~0.
    assert _haversine_m(48.8566, 2.3522, 48.8566, 2.3522) < 1
    # ~0.001° de latitude ≈ 111 m -> dans le seuil (200 m).
    assert _location_ok_geo(48.8566, 2.3522, 48.8576, 2.3522) is True
    # ~0.01° ≈ 1.1 km -> hors seuil (homonyme dans un autre quartier).
    assert _location_ok_geo(48.8566, 2.3522, 48.8666, 2.3522) is False
    # Coordonnées manquantes -> indécidable (repli texte côté appelant).
    assert _location_ok_geo(None, None, 48.85, 2.35) is None
    assert _location_ok_geo(48.85, 2.35, None, None) is None


def test_places_match_decision_geo_confirms_never_vetoes():
    """Décision de match : le type CHR est un gate dur ; la proximité géo
    CONFIRME mais n'oppose jamais de veto (le siège Sirene est souvent loin du
    local) -> une distance élevée renvoie au texte, elle ne rejette pas."""
    from app.ingestion.enrichment.places import _match_ok

    near = dict(place_lat=48.8678, place_lon=2.3567)
    far = dict(place_lat=48.8200, place_lon=2.3000)
    anchor = dict(anchor_lat=48.8678, anchor_lon=2.3567)
    addr = "41 Rue X, 75003 Paris"      # texte OK (CP+ville)
    bad_addr = "10 Rue Y, 69001 Lyon"   # texte KO

    # Type non-CHR -> rejet immédiat, peu importe la localisation.
    assert _match_ok("hair_salon", addr, "75003", "Paris", **near, **anchor) is False
    # Proximité géo -> confirmé fort, accepté MÊME si le texte échoue.
    assert _match_ok("restaurant", bad_addr, "75003", "Paris", **near, **anchor) is True
    # Géo LOIN mais texte OK -> accepté (la distance ne veto pas).
    assert _match_ok("restaurant", addr, "75003", "Paris", **far, **anchor) is True
    # Géo loin ET texte KO -> rejeté (le texte décide).
    assert _match_ok("restaurant", bad_addr, "75003", "Paris", **far, **anchor) is False
    # Sans coords d'ancrage -> repli texte : CP ou ville suffit.
    assert _match_ok("restaurant", addr, "75003", "Paris") is True
    assert _match_ok("restaurant", bad_addr, "75003", "Paris") is False


def test_process_candidate_dedup_upsert():
    """Dédup persistante : ré-ingérer le même (source, source_ref) MET À JOUR
    le lead au lieu d'en créer un doublon."""
    from sqlmodel import SQLModel, Session, create_engine, select
    from app.models import Opportunity
    from app.ingestion.base import LeadCandidate
    from app.ingestion.pipeline import _process_candidate, IngestStats

    engine = create_engine("sqlite://")  # base en mémoire, isolée
    SQLModel.metadata.create_all(engine)

    def make(name):
        return LeadCandidate(
            source="bodacc", source_ref="REF-1", establishment_name=name,
            city="Paris", main_signal="création récente", detection_date=date(2026, 6, 1),
            classification_text="restauration traditionnelle",
        )

    with Session(engine) as session:
        stats = IngestStats(source="bodacc")
        _process_candidate(session, make("Bistrot V1"), stats, set(), None)
        _process_candidate(session, make("Bistrot V2"), stats, set(), None)
        session.commit()

        rows = session.exec(select(Opportunity).where(Opportunity.source_ref == "REF-1")).all()
        assert len(rows) == 1                 # pas de doublon
        assert rows[0].establishment_name == "Bistrot V2"  # mis à jour
        assert stats.created == 1 and stats.updated == 1


def test_contact_pass_rescores_on_review_count():
    """La passe contact stocke review_count ET re-score le lead (la fraîcheur
    n'est connue qu'après le match Places)."""
    from app.models import Opportunity
    from app.ingestion.pipeline import _contact_enrich_one, ContactStats
    from app.ingestion.enrichment.contact_enricher import ContactInfo

    opp = Opportunity(
        establishment_name="Le Test", establishment_type="restaurant", city="Paris",
        address="1 Rue X, 75011 Paris", main_signal="création récente",
        secondary_signals=[], detection_date=date(2026, 6, 1), estimated_timing="J-30",
        probable_needs=["luminaires"], opportunity_score=5, recommended_channel="telephone",
        source="bodacc", source_ref="R1",
    )

    class FakeEnricher:  # match FIABLE (geo) -> on fait confiance au nb d'avis.
        def enrich(self, *a, **k):
            return ContactInfo(phone="0102030405", review_count=4, match_basis="geo")

    class FakeSirene:
        def lookup(self, siren):
            return None

    _contact_enrich_one(opp, FakeEnricher(), FakeSirene(), ContactStats())
    assert opp.review_count == 4
    assert opp.phone == "0102030405"

    # Le re-score recalcule depuis zéro (et dépend de date.today()) : on compare
    # au compute_score de référence pour les mêmes entrées, sans réimplémenter.
    from app.services.scoring import compute_score
    from app.services.segment import classify_segment
    base_inputs = dict(
        main_signal=opp.main_signal, secondary_signals=opp.secondary_signals,
        detection_date=opp.detection_date, probable_needs=opp.probable_needs,
        decision_maker=opp.decision_maker, recommended_channel=opp.recommended_channel,
        segment=classify_segment(opp.establishment_type, opp.naf, opp.establishment_name),
    )
    no_review = compute_score(**base_inputs, review_count=None).score
    expected = compute_score(**base_inputs, review_count=4).score
    assert opp.opportunity_score == expected
    assert expected == min(10, no_review + 1)   # la fraîcheur ajoute bien +1
    assert "peu d'avis" in opp.score_reason


def test_contact_pass_does_not_reclass_creation_via_reviews():
    """Non-régression : création/reprise vient du REGISTRE (origineFonds). La
    passe contact ne reclasse PLUS via les avis Places (retrait du misfire (b)) ;
    elle stocke review_count et re-score, c'est tout."""
    from app.models import Opportunity
    from app.ingestion.pipeline import _contact_enrich_one, ContactStats
    from app.ingestion.enrichment.contact_enricher import ContactInfo

    opp = Opportunity(
        establishment_name="Le Test", establishment_type="restaurant", city="Paris",
        address="1 Rue X, 75011 Paris", main_signal="création récente",
        secondary_signals=[], detection_date=date(2026, 6, 1), estimated_timing="J-30",
        probable_needs=["luminaires"], recommended_channel="telephone",
        source="bodacc", source_ref="R1",
    )

    class Enr:
        def enrich(self, *a, **k):
            return ContactInfo(phone="0102030405", review_count=900, match_basis="geo")

    class NoSirene:
        def lookup(self, siren): return None

    _contact_enrich_one(opp, Enr(), NoSirene(), ContactStats())
    assert opp.main_signal == "création récente"   # PAS reclassé malgré 900 avis
    assert opp.review_count == 900
    assert opp.estimated_timing == "J-30"
    assert opp.contact_confidence == "haute"       # tél via match géo


def test_contact_pass_ignores_reviews_on_unreliable_match():
    """Cas BEAR YTD : match Places par nom+ville mais nom DISCORDANT (Bearsden)
    -> confiance basse -> on n'affiche/score PAS les 424 avis (autre lieu)."""
    from app.models import Opportunity
    from app.ingestion.pipeline import _contact_enrich_one, ContactStats
    from app.ingestion.enrichment.contact_enricher import ContactInfo

    opp = Opportunity(
        establishment_name="BEAR YTD", establishment_type="coffee shop", city="Paris",
        address="38 rue Yves Toudic, 75010 Paris", main_signal="création récente",
        secondary_signals=[], detection_date=date(2026, 6, 25), estimated_timing="J-30",
        probable_needs=["mobilier"], recommended_channel="telephone",
        source="bodacc", source_ref="B1",
        review_count=999,  # valeur périmée déjà en base (passe précédente)
    )

    class Enr:  # Places a renvoyé "Bearsden Paris" (424 avis) par nom+ville.
        def enrich(self, *a, **k):
            return ContactInfo(phone="0145354923", review_count=424,
                               match_basis="text", place_name="Bearsden Paris")

    class NoSirene:
        def lookup(self, siren): return None

    _contact_enrich_one(opp, Enr(), NoSirene(), ContactStats())
    assert opp.contact_confidence == "basse"   # nom discordant
    assert opp.review_count is None             # 424 ET le 999 périmé effacés


def test_contact_quality_email_classification():
    from app.services.contact_quality import classify_email, is_role_based_email

    assert is_role_based_email("contact@resto.fr") is True
    assert classify_email("contact@resto.fr") == "etablissement"
    assert classify_email("resa@resto.fr") == "etablissement"
    assert classify_email("marie.dupont@resto.fr") == "decideur"        # nominatif
    assert classify_email("jean@resto.fr", decision_maker="Jean Martin") == "decideur"
    assert classify_email("jean@resto.fr") == "etablissement"           # ambigu -> sûr
    assert classify_email(None) is None


def test_contact_quality_confidence_levels():
    from app.services.contact_quality import establishment_confidence, decision_maker_confidence

    # Précision d'abord : établissement fiable UNIQUEMENT si géo-confirmé.
    assert establishment_confidence("geo") == "haute"
    assert establishment_confidence("text") == "basse"   # match par nom -> pas fiable
    assert establishment_confidence(None) == "basse"
    # Décideur : haute seulement si l'email est corroboré par le nom.
    assert decision_maker_confidence("marie.dupont@x.fr", "Marie Dupont") == "haute"
    assert decision_maker_confidence("contact@x.fr", "Marie Dupont") == "basse"
    assert decision_maker_confidence(None, "Marie Dupont") == "basse"


def test_places_match_basis():
    from app.ingestion.enrichment.places import _match_basis

    near = dict(place_lat=48.8678, place_lon=2.3567)
    far = dict(place_lat=48.8200, place_lon=2.3000)
    anchor = dict(anchor_lat=48.8678, anchor_lon=2.3567)
    addr = "41 Rue X, 75003 Paris"

    assert _match_basis("restaurant", addr, "75003", "Paris", **near, **anchor) == "geo"
    assert _match_basis("restaurant", addr, "75003", "Paris", **far, **anchor) == "text"
    assert _match_basis("restaurant", "10 Rue Y, 69001 Lyon", "75003", "Paris", **far, **anchor) is None
    assert _match_basis("hair_salon", addr, "75003", "Paris", **near, **anchor) is None
    assert _match_basis("restaurant", addr, "75003", "Paris") == "text"


def test_bodacc_parses_dirigeants_from_administration():
    """Pour une société (pm), les décideurs viennent du champ `administration`
    (TOUS : Président, DG, Gérant…), pas du nom de la société."""
    from app.ingestion.bodacc import _parse_dirigeant, _parse_dirigeants

    admin = "Président : Afif, Samuel Serge Elie, Directeur général : Journo, Victor Isaac"
    # Liste complète, Président en tête.
    assert _parse_dirigeants(admin) == [
        "Samuel Afif, Président",
        "Victor Journo, Directeur général",
    ]
    # Principal seul.
    assert _parse_dirigeant(admin) == "Samuel Afif, Président"
    assert _parse_dirigeant("Gérant : Martin, Marie") == "Marie Martin, Gérant"
    assert _parse_dirigeants(None) == []
    assert _parse_dirigeants("") == []


def test_bodacc_company_uses_administration_for_decision_makers():
    """Bout-en-bout : une SAS dont listepersonnes est la société -> on capture
    TOUS les dirigeants (BEAR YTD = Afif + Journo)."""
    record = {
        "id": "A2026ADMIN",
        "familleavis": "creation",
        "commercant": "BEAR YTD",
        "ville": "Paris",
        "cp": "75010",
        "dateparution": "2026-06-25",
        "listepersonnes": (
            '{"personne": {"typePersonne": "pm", "denomination": "BEAR YTD",'
            ' "activite": "coffee shop, salon de thé",'
            ' "administration": "Président : Afif, Samuel Serge Elie,'
            ' Directeur général : Journo, Victor Isaac"}}'
        ),
        "listeprecedentproprietaire": None,
        "listeprecedentexploitant": None,
    }
    c = BodaccConnector().to_candidates([record])[0]
    assert c.establishment_name == "BEAR YTD"
    assert c.decision_maker == "Samuel Afif, Président"
    assert c.dirigeants == ["Samuel Afif, Président", "Victor Journo, Directeur général"]


def test_bodacc_extracts_previous_siren():
    from app.ingestion.bodacc import _extract_previous_siren

    val = ('{"personne": {"denomination": "RESTO X",'
           ' "numeroImmatriculation": {"numeroIdentification": "384 821 682",'
           ' "codeRCS": "RCS"}, "typePersonne": "pm"}}')
    assert _extract_previous_siren(val) == "384821682"
    assert _extract_previous_siren("ANCIEN EXPLOITANT SARL") is None  # texte brut
    assert _extract_previous_siren(None) is None


def test_bodacc_parses_activity_start_date():
    """La date de début d'activité (acte.dateCommencementActivite) est captée."""
    from datetime import date as _date

    record = {
        "id": "A2026ACT",
        "familleavis": "creation",
        "commercant": "Le Futur Bistrot",
        "ville": "Paris",
        "cp": "75011",
        "dateparution": "2026-06-20",
        "listepersonnes": '{"personne": {"denomination": "Le Futur Bistrot",'
        ' "activite": "restauration traditionnelle"}}',
        "acte": '{"dateImmatriculation": "2026-06-17",'
        ' "dateCommencementActivite": "2026-08-01"}',
    }
    c = BodaccConnector().to_candidates([record])[0]
    assert c.activity_start_date == _date(2026, 8, 1)


def test_bodacc_origine_fonds_drives_creation_vs_reprise():
    import json as _json

    def rec(origine):
        return {
            "id": "A2026ORIG",
            "familleavis": "creation",
            "commercant": "Bistrot X",
            "ville": "Paris",
            "cp": "75011",
            "dateparution": "2026-06-20",
            "listepersonnes": '{"personne": {"denomination": "Bistrot X",'
            ' "activite": "restauration traditionnelle"}}',
            "listeetablissements": _json.dumps(
                {"etablissement": {"origineFonds": origine, "activite": "restauration"}}
            ),
            "listeprecedentproprietaire": None,
            "listeprecedentexploitant": None,
        }

    c1 = BodaccConnector().to_candidates([rec("Création d'un fonds de commerce")])[0]
    assert c1.main_signal == "création récente"
    c2 = BodaccConnector().to_candidates([rec("Achat au précédent exploitant ANCIENNE SARL")])[0]
    assert c2.main_signal == "reprise"


def test_scoring_cross_signal_counts_families_not_labels():
    """Le bonus 'signaux croisés' compte les familles distinctes, pas les
    libellés : reprise + changement propriétaire = 1 famille."""
    from datetime import date as _date
    from app.services.scoring import compute_score, _signal_families

    # Logique de comptage (pure).
    assert _signal_families({"reprise", "changement propriétaire"}) == {"takeover"}
    assert _signal_families({"création récente", "recrutement"}) == {"opening", "recruitment"}
    assert _signal_families(
        {"reprise", "changement propriétaire", "recrutement"}
    ) == {"takeover", "recruitment"}
    # Un libellé inconnu compte pour lui-même (reste croisé s'il diffère).
    assert _signal_families({"reprise", "annonce presse locale"}) == {
        "takeover", "annonce presse locale"
    }

    # Intégration : même famille -> AUCUN bonus croisé (cas isolé : "changement
    # propriétaire" n'ajoute pas de bonus de nature en plus de "reprise").
    base = dict(
        detection_date=_date(2026, 6, 20), probable_needs=["luminaires"],
        decision_maker=None, recommended_channel="telephone", today=_date(2026, 6, 28),
    )
    same_family = compute_score(main_signal="reprise",
                                secondary_signals=["changement propriétaire"], **base)
    takeover_alone = compute_score(main_signal="reprise", secondary_signals=[], **base)
    assert same_family.score == takeover_alone.score
    assert "signaux croisés" not in same_family.reason


def test_scoring_inventory_signals_no_cross_bonus():
    """Motif #2 (bonus) : un lead chain_multisite du funnel Insta porte
    « établissement en activité » + « extension multi-sites ». Ces deux libellés
    NEUTRES appartiennent à la MÊME famille (inventaire) -> AUCUN bonus « signaux
    croisés » accidentel (ils décrivaient auparavant 2 familles inconnues)."""
    from datetime import date as _date
    from app.services.scoring import compute_score, _signal_families

    assert _signal_families(
        {"établissement en activité", "extension multi-sites"}
    ) == {"inventaire"}

    base = dict(
        detection_date=_date(2026, 6, 20), probable_needs=["luminaires"],
        decision_maker=None, recommended_channel="telephone", today=_date(2026, 6, 28),
    )
    chain = compute_score(main_signal="établissement en activité",
                          secondary_signals=["extension multi-sites"], **base)
    neutral_alone = compute_score(main_signal="établissement en activité",
                                  secondary_signals=[], **base)
    assert chain.score == neutral_alone.score
    assert "signaux croisés" not in chain.reason


def test_scoring_freshness_from_review_count():
    from datetime import date as _date
    from app.services.scoring import compute_score

    args = dict(
        main_signal="création récente", secondary_signals=[],
        detection_date=_date(2026, 5, 9), probable_needs=["luminaires"],
        decision_maker=None, recommended_channel="telephone",
        today=_date(2026, 6, 28),
    )
    base = compute_score(**args)
    fresh = compute_score(**args, review_count=5)         # tout récent -> +1
    established = compute_score(**args, review_count=800)  # installé -> -1
    assert fresh.score == min(10, base.score + 1)
    assert established.score == max(0, base.score - 1)
    assert "peu d'avis" in fresh.reason
    assert "déjà installé" in established.reason
    # review_count inconnu -> aucun effet.
    assert compute_score(**args, review_count=None).score == base.score


def test_segment_classifier():
    from app.services.segment import classify_segment

    assert classify_segment("restaurant") == "venue"
    assert classify_segment("hôtel") == "venue"
    assert classify_segment("traiteur") == "service"
    assert classify_segment("restaurant", naf="56.21Z") == "service"  # NAF prime
    assert classify_segment("café") == "venue"


def test_scoring_penalizes_service_segment():
    from datetime import date as _date
    from app.services.scoring import compute_score

    # Cas volontairement faible (non plafonné) : rénovation, signal de 50j, 1 besoin.
    args = dict(
        main_signal="rénovation", secondary_signals=[],
        detection_date=_date(2026, 5, 9), probable_needs=["luminaires"],
        decision_maker=None, recommended_channel="telephone",
        today=_date(2026, 6, 28),
    )
    base = compute_score(**args)
    svc = compute_score(**args, segment="service")
    assert svc.score == max(0, base.score - 2)
    assert "à domicile" in svc.reason


def test_website_scraper_extracts_contacts():
    from app.ingestion.enrichment.website_scraper import extract_from_html

    html = """
    <html><body>
      Contactez-nous : <a href="mailto:bonjour@maison-oria.fr">email</a>
      <a href="https://instagram.com/maisonoria">insta</a>
      <a href="https://facebook.com/maisonoria">fb</a>
      <a href="tel:+33145678910">appeler</a>
      <img src="logo@2x.png"> noise sentry@sentry.io
    </body></html>
    """
    c = extract_from_html(html, site_domain="maison-oria.fr")
    assert c["email"] == "bonjour@maison-oria.fr"
    assert c["instagram"] == "maisonoria"
    assert c["facebook"] == "maisonoria"
    assert "+33145678910" in c["phone"]


def test_website_scraper_filters_junk_and_ignored():
    from app.ingestion.enrichment.website_scraper import extract_from_html

    # Que des artefacts : aucun email valide, insta=post à ignorer.
    html = 'src="x@2x.png" <a href="https://instagram.com/p/abc123">post</a> sentry@wixpress.com'
    c = extract_from_html(html, site_domain="exemple.fr")
    assert c["email"] is None
    assert c["instagram"] is None


def test_website_scraper_ignores_form_placeholder_email():
    from app.ingestion.enrichment.website_scraper import extract_from_html

    # Placeholder de formulaire de réservation + vrai email en mailto.
    html = (
        '<input type="email" placeholder="sophie@email.com">'
        '<a href="mailto:resa@koko-odessa.fr">réserver</a>'
    )
    assert extract_from_html(html, site_domain="koko-odessa.fr")["email"] == "resa@koko-odessa.fr"
    # Placeholder seul (pas de mailto) -> on n'invente pas d'email.
    only_placeholder = '<input type="email" placeholder="sophie@email.com">'
    assert extract_from_html(only_placeholder, site_domain="koko-odessa.fr")["email"] is None


def test_refresh_closure_and_heartbeat():
    """La passe refresh : détecte la fermeture (Sirene état != A) -> closed_at +
    status perdu + Signal 'fermé' ; et pose le heartbeat last_checked_at."""
    from sqlmodel import SQLModel, Session, create_engine, select
    from app.models import Opportunity, Signal
    from app.ingestion.pipeline import _refresh_one, RefreshStats

    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)

    def make(ref, siren):
        return Opportunity(
            establishment_name="X", establishment_type="restaurant", city="Paris",
            address="", main_signal="reprise", secondary_signals=[],
            detection_date=date(2026, 6, 1), estimated_timing="J-60", probable_needs=[],
            source="bodacc", source_ref=ref, siren=siren,
        )

    class ClosedSirene:
        def lookup(self, siren): return {"etat_administratif": "F", "siege": {}}

    class ActiveSirene:
        def lookup(self, siren): return {"etat_administratif": "A", "siege": {}}

    with Session(engine) as s:
        closed = make("R1", "123456789")
        s.add(closed); s.commit(); s.refresh(closed)
        _refresh_one(s, closed, ClosedSirene(), RefreshStats()); s.commit()
        assert closed.closed_at is not None
        assert closed.status == "perdu"
        assert closed.last_checked_at is not None
        sigs = s.exec(select(Signal).where(Signal.opportunity_id == closed.id)).all()
        assert any(sg.signal_type == "fermé" for sg in sigs)

        active = make("R2", "987654321")
        s.add(active); s.commit(); s.refresh(active)
        _refresh_one(s, active, ActiveSirene(), RefreshStats()); s.commit()
        assert active.closed_at is None
        assert active.last_checked_at is not None  # heartbeat -> fraîcheur remise à zéro


def test_lifecycle_stage():
    from datetime import date as _date
    from app.services.lifecycle import lifecycle_stage

    today = _date(2026, 6, 29)
    # Fermé prime tout.
    assert lifecycle_stage("création récente", None, _date(2026, 6, 20), today, closed=True) == "fermé"
    # Beaucoup d'avis -> établi (même si signal d'ouverture).
    assert lifecycle_stage("création récente", 400, _date(2026, 6, 20), today) == "établi"
    # Quelques avis -> ouvert récemment.
    assert lifecycle_stage("création récente", 8, _date(2026, 6, 20), today) == "ouvert récemment"
    # Signal d'ouverture récent, pas d'avis -> pré-ouverture.
    assert lifecycle_stage("création récente", None, _date(2026, 6, 20), today) == "pré-ouverture"
    # Signal d'ouverture ancien -> ouvert récemment (plus pré-ouverture).
    assert lifecycle_stage("création récente", None, _date(2026, 3, 1), today) == "ouvert récemment"
    # Reprise (pas famille ouverture) -> ouvert récemment.
    assert lifecycle_stage("reprise", None, _date(2026, 6, 20), today) == "ouvert récemment"
    # Date de début d'activité FUTURE -> pré-ouverture (fiable, prime l'heuristique
    # même pour une reprise). PASSÉE -> ouvert récemment.
    assert lifecycle_stage("reprise", None, _date(2026, 6, 20), today,
                           activity_start_date=_date(2026, 8, 1)) == "pré-ouverture"
    assert lifecycle_stage("reprise", None, _date(2026, 6, 20), today,
                           activity_start_date=_date(2026, 6, 10)) == "ouvert récemment"
    # Des avis priment la date (si avis -> forcément déjà ouvert/établi).
    assert lifecycle_stage("création récente", 500, _date(2026, 6, 20), today,
                           activity_start_date=_date(2026, 8, 1)) == "établi"
    # Vieux local repris (origine >= 2 ans) -> établi ("établi mais chaud" : le
    # stage décrit le lieu ancien, la chaleur reste chaude via la reprise récente).
    assert lifecycle_stage("reprise", None, _date(2026, 6, 20), today,
                           venue_origin_date=_date(2015, 1, 1)) == "établi"
    # Repris d'un local JEUNE (origine récente) -> pas établi.
    assert lifecycle_stage("reprise", None, _date(2026, 6, 20), today,
                           venue_origin_date=_date(2026, 1, 1)) == "ouvert récemment"
    # Label de cycle de vie persisté established/chain_multisite (fiche Insta « en
    # base » : signal NEUTRE, ni avis, ni origine, ni date d'activité) -> établi.
    # Sans le label, ce cas retomberait à tort sur "ouvert récemment" (repli final).
    assert lifecycle_stage("établissement en activité", None, _date(2026, 6, 20),
                           today, lifecycle_label="established") == "établi"
    assert lifecycle_stage("établissement en activité", None, _date(2026, 6, 20),
                           today, lifecycle_label="chain_multisite") == "établi"
    # Cohérence même quand la détection est récente (pas de bascule pré-ouverture).
    assert lifecycle_stage("établissement en activité", None, today, today,
                           lifecycle_label="established") == "établi"
    # Un label 'unknown' NE force PAS établi (repli heuristique inchangé).
    assert lifecycle_stage("établissement en activité", None, _date(2026, 6, 20),
                           today, lifecycle_label="unknown") == "ouvert récemment"
    # Le label ne masque pas une fermeture (fermé prime tout).
    assert lifecycle_stage("établissement en activité", None, _date(2026, 6, 20),
                           today, closed=True, lifecycle_label="established") == "fermé"


def test_lifecycle_heat():
    from datetime import date as _date
    from app.services.lifecycle import heat

    today = _date(2026, 6, 29)
    # Moment d'achat récent -> chaud, quel que soit le stage ("établi mais chaud").
    assert heat("recrutement", _date(2026, 6, 20), today) == "chaud"        # 9 j
    assert heat("reprise", _date(2026, 3, 31), today) == "tiède"            # ~90 j
    assert heat("création récente", _date(2026, 1, 1), today) == "froid"    # vieux
    # Signal non-achat -> froid.
    assert heat("annonce presse locale", _date(2026, 6, 28), today) == "froid"


def test_lifecycle_freshness():
    from datetime import date as _date
    from app.services.lifecycle import freshness

    today = _date(2026, 6, 29)
    assert freshness(_date(2026, 6, 20), today) == "fraîche"       # 9 j
    assert freshness(_date(2026, 5, 1), today) == "à rafraîchir"   # ~59 j
    assert freshness(_date(2026, 1, 1), today) == "périmée"        # ~180 j
    assert freshness(None, today) == "à rafraîchir"


def test_instagram_discover_filters_chr_idf():
    from app.ingestion.instagram import discover

    posts = [
        {"ownerUsername": "resto_paris", "ownerFullName": "Le Nouveau Bistrot",
         "caption": "Ouverture prochaine !", "hashtags": ["ouvertureprochaine"],
         "locationName": "Paris, France"},
        {"ownerUsername": "gym_nanterre", "ownerFullName": "Fitness Club",
         "caption": "Salle de sport #ouvertureprochaine", "hashtags": [],
         "locationName": "Nanterre"},  # pas CHR
        {"ownerUsername": "resto_nice", "ownerFullName": "Pizzeria Bella",
         "caption": "Notre restaurant ouvre", "hashtags": [], "locationName": "Nice"},  # pas IdF
        {"ownerUsername": "cafe_92", "ownerFullName": "Café Lumo",
         "caption": "Nouveau café à Boulogne 92100", "hashtags": [], "locationName": None},
        {"ownerUsername": "resto_paris", "ownerFullName": "doublon",
         "caption": "resto", "locationName": "Paris"},  # doublon handle
    ]
    got = discover(posts)
    handles = [d["handle"] for d in got]
    assert "resto_paris" in handles       # CHR + Paris
    assert "cafe_92" in handles           # CHR + CP 92
    assert "gym_nanterre" not in handles  # pas CHR
    assert "resto_nice" not in handles    # CHR mais pas IdF
    assert handles.count("resto_paris") == 1  # dédup par handle
    assert next(d for d in got if d["handle"] == "cafe_92")["type"] == "café"


def test_city_from_location_keeps_compound_hyphenated_names():
    # Dette connue (HANDOFF.md « Extraction de ville cassée ») : le découpage sur
    # TOUT tiret mutilait les villes composées (Château-Gontier -> "Château").
    # Fix : ne découper que sur la virgule et le tiret ESPACÉ (' - '), jamais le
    # tiret collé des noms composés.
    from app.ingestion.instagram import _city_from_location

    assert _city_from_location("Château-Gontier") == "Château-Gontier"
    assert _city_from_location("Paris, France") == "Paris"
    assert _city_from_location("Le Mans - Centre") == "Le Mans"
    assert _city_from_location("Saint-Denis") == "Saint-Denis"


def test_osm_name_matching():
    from app.ingestion.enrichment.osm import _name_matches

    assert _name_matches("Maison Oria", "Restaurant Maison Oria") is True
    assert _name_matches("Café Lumo", "Lumo Coffee") is True
    assert _name_matches("Le Bar", "Pharmacie Centrale") is False


def test_bodacc_build_where_uses_since_date():
    from datetime import date as _date

    c = BodaccConnector()
    where = c._build_where(
        since_days=90, departments=["75", "92"], families=["creation"],
        since_date=_date(2026, 6, 1),
    )
    assert 'dateparution >= "2026-06-01"' in where
    assert 'numerodepartement in ("75", "92")' in where
    assert 'familleavis in ("creation")' in where


def test_naf_classifier_maps_and_filters():
    assert classify_naf("56.10A") == "restaurant"
    assert classify_naf("56.10C") == "restaurant"
    assert classify_naf("55.10Z") == "hôtel"
    assert classify_naf("56.30Z") == "bar"
    assert classify_naf("56.21Z") == "traiteur"
    # NAF non-CHR -> écarté (kill des faux positifs du plein-texte)
    assert classify_naf("47.11D") is None
    assert classify_naf("64.20Z") is None  # holding
    assert classify_naf("56.29A") is None  # restauration collective exclue
    assert classify_naf(None) is None


def test_naf_classifier_keyword_refinement():
    # Le NAF donne "restaurant"/"bar" ; le mot-clé affine le sous-type.
    assert classify_naf("56.10A", "Brasserie du Coin") == "brasserie"
    assert classify_naf("56.30Z", "Le Coffee Shop branché") == "coffee shop"
    assert classify_naf("56.10A", "Resto sans précision") == "restaurant"


def test_sirene_apply_improves_name_and_filters_closed():
    cand = LeadCandidate(
        source="bodacc",
        source_ref="X1",
        establishment_name="CRESSON, Sandrine",  # nom civil
        city="Massy",
        main_signal="création récente",
        detection_date=date(2026, 6, 1),
        siren="123456789",
    )
    data = {
        "etat_administratif": "A",
        "activite_principale": "56.21Z",
        "siege": {
            "activite_principale": "56.21Z",
            "liste_enseignes": ["SANDY CHEZ VOUS"],
            "adresse": "10 RUE AUX LEZARDS 77310 SAINT-FARGEAU-PONTHIERRY",
        },
    }
    apply_sirene_data(cand, data)
    assert cand.establishment_name == "SANDY CHEZ VOUS"
    assert cand.naf == "56.21Z"
    assert "LEZARDS" in cand.address
    assert cand.closed is False


def test_sirene_marks_closed_establishment():
    cand = LeadCandidate(
        source="bodacc",
        source_ref="X2",
        establishment_name="Vieux Resto",
        city="Paris",
        main_signal="reprise",
        detection_date=date(2026, 6, 1),
        siren="987654321",
    )
    apply_sirene_data(cand, {"etat_administratif": "F", "siege": {}})
    assert cand.closed is True


def test_bodacc_vente_maps_to_reprise():
    record = {
        "id": "A2026TEST03",
        "familleavis": "vente",
        "commercant": "Brasserie du Coin",
        "ville": "Versailles",
        "cp": "78000",
        "dateparution": "2026-06-18",
        "listepersonnes": '{"personne": {"denomination": "Brasserie du Coin",'
        ' "activite": "brasserie restaurant"}}',
        "listeprecedentproprietaire": "ANCIEN PROPRIO",
    }
    candidates = BodaccConnector().to_candidates([record])
    assert len(candidates) == 1
    assert candidates[0].main_signal == "reprise"
    assert "changement propriétaire" in candidates[0].secondary_signals


# =========================================================================
# Fixes passe contact (branche feature/contact-quality) — audit-enrichissement
# =========================================================================


def test_strong_name_match_rejects_homonym():
    """Fix 1 — concordance de nom FORTE : « Marco Del Caffé » NE concorde PAS
    avec « Café Marco Polo » (un seul token commun, aucun sous-ensemble) ->
    aucun contact écrit ; un nom réellement concordant passe."""
    from app.ingestion.enrichment.contact_enricher import _strong_name_match

    assert _strong_name_match("Marco Del Caffé", "Café Marco Polo") is False
    assert _strong_name_match("Peace Museum Paris & Cafe", "Café de la Paix") is False
    assert _strong_name_match("Giorgina", "Giorgina Ristorante Nogent") is True
    assert _strong_name_match("Le Petit Marseille", "Le Petit Marseille") is True
    assert _strong_name_match("Giorgina", None) is False
    assert _strong_name_match("", "Café Marco Polo") is False


def test_enrich_identity_lock_blocks_text_homonym(monkeypatch):
    """Fix 1 — un match Places par TEXTE au nom discordant (homonyme) n'écrit
    RIEN ; le même match par géo, ou par nom concordant, remplit."""
    from app.ingestion.enrichment import contact_enricher as ce

    def fake_places_homonym(name, **k):
        return {"matched": True, "phone": "0140073636", "website": "https://cafedelapaix.fr",
                "review_count": 900, "match_basis": "text", "display_name": "Café de la Paix"}

    monkeypatch.setattr(ce, "lookup_places", fake_places_homonym)
    info = ce.ContactEnricher().enrich("Peace Museum Paris & Cafe", None, None, city="Paris")
    assert info.phone is None and info.website is None   # homonyme -> rien
    assert info.match_basis is None

    def fake_places_geo(name, **k):
        # Géo-confirmé ET nom concordant (au moins un token distinctif commun) ->
        # rempli. La géo confirme un LIEU, mais l'identité reste exigée.
        return {"matched": True, "phone": "0148772136", "website": "https://sushicharlesvii.fr",
                "review_count": 12, "match_basis": "geo", "display_name": "Sushi Charles VII Nogent"}

    monkeypatch.setattr(ce, "lookup_places", fake_places_geo)
    info = ce.ContactEnricher().enrich("Sushi Charles VII", None, None, city="Nogent")
    assert info.phone == "0148772136"                    # géo-confirmé + nom -> rempli
    assert info.website == "https://sushicharlesvii.fr"

    def fake_places_name(name, **k):
        return {"matched": True, "phone": "0102030405", "website": "https://giorgina.fr",
                "review_count": 3, "match_basis": "text", "display_name": "Giorgina Ristorante"}

    monkeypatch.setattr(ce, "lookup_places", fake_places_name)
    info = ce.ContactEnricher().enrich("Giorgina", None, None, city="Nogent-sur-Marne")
    assert info.phone == "0102030405"                    # nom concordant fort -> rempli


def test_enrich_geo_requires_identity_soka_bolkiri(monkeypatch):
    """Cas réel SOKA FOOD / Bolkiri (Saint-Gratien 95210). Une pizzeria fraîchement
    créée (pas encore sur Places) est géo-confirmée sur son VOISIN Bolkiri (même
    rue, 218 avis). La proximité confirme un LIEU, pas une IDENTITÉ :
      (a) géo-confirmé mais ZÉRO recoupement de nom + 218 avis -> RIEN d'écrit ;
      (b) géo-confirmé, zéro recoupement, SANS avis -> rejet aussi (identité) ;
      (c) enseigne mono-token concordante + géo -> acceptée (non-régression) ;
      (d) géo + recoupement OK + peu d'avis + création récente -> acceptée."""
    from app.ingestion.enrichment import contact_enricher as ce
    from datetime import date as _date

    # (a) SOKA FOOD géo-confirmé sur « Bolkiri Saint-Gratien », 218 avis.
    def fake_bolkiri(name, **k):
        return {"matched": True, "phone": "0183846538",
                "website": "https://restaurants.bolkiri.fr", "review_count": 218,
                "match_basis": "geo", "display_name": "Bolkiri Saint-Gratien"}

    monkeypatch.setattr(ce, "lookup_places", fake_bolkiri)
    info = ce.ContactEnricher().enrich(
        "SOKA FOOD", None, None, city="Saint-Gratien", postal="95210",
        main_signal="création récente",
    )
    assert info.phone is None and info.website is None    # voisin -> rien
    assert info.review_count is None and info.match_basis is None

    # (b) Zéro recoupement de nom SANS avis : rejeté par le verrou d'identité seul.
    def fake_zero_overlap(name, **k):
        return {"matched": True, "phone": "0102030405", "website": "https://voisin.fr",
                "review_count": 0, "match_basis": "geo", "display_name": "Le Voisin"}

    monkeypatch.setattr(ce, "lookup_places", fake_zero_overlap)
    info = ce.ContactEnricher().enrich(
        "SOKA FOOD", None, None, city="Saint-Gratien", main_signal="création récente",
    )
    assert info.phone is None and info.website is None
    assert info.match_basis is None

    # (c) Enseigne mono-token concordante + géo (MOKA-style) -> acceptée.
    def fake_mono(name, **k):
        return {"matched": True, "phone": "0148772136", "website": "https://moka.fr",
                "review_count": 8, "match_basis": "geo", "display_name": "Moka"}

    monkeypatch.setattr(ce, "lookup_places", fake_mono)
    info = ce.ContactEnricher().enrich(
        "Moka", None, None, city="Paris", main_signal="création récente",
    )
    assert info.phone == "0148772136"                     # mono-token + géo -> rempli
    assert info.review_count == 8

    # (d) Recoupement OK + 12 avis + création récente -> pas de sur-blocage.
    def fake_ok(name, **k):
        return {"matched": True, "phone": "0140506070", "website": "https://sokafood.fr",
                "review_count": 12, "match_basis": "geo", "display_name": "Soka Food Pizzeria"}

    monkeypatch.setattr(ce, "lookup_places", fake_ok)
    info = ce.ContactEnricher().enrich(
        "SOKA FOOD", None, None, city="Saint-Gratien", main_signal="création récente",
        activity_start_date=_date.today(),
    )
    assert info.phone == "0140506070"                     # nom OK + peu d'avis -> rempli
    assert info.review_count == 12


def test_fresh_conflict_only_applies_to_weak_identity(monkeypatch):
    """Décision produit — la contradiction d'avis ne s'applique qu'à identité
    FAIBLE. (a) géo + nom FORT + 111 avis + création récente -> REMPLI (cas
    Giorgina réel : 111 avis, ouvert mi-juin, just_opened confirmé par le
    propriétaire — un resto parisien hype prend 100 avis en un mois) ;
    (b) géo + overlap mono-token partiel + 300 avis + création récente ->
    rejeté (zone grise, voisin probable) ; (c) SOKA/Bolkiri inchangé."""
    from app.ingestion.enrichment import contact_enricher as ce

    # (a) Identité FORTE (géo + nom fort) : le détecteur s'efface.
    def fake_giorgina(name, **k):
        return {"matched": True, "phone": "0184161110",
                "website": "https://www.restaurant-giorgina.com/",
                "review_count": 111, "match_basis": "geo", "display_name": "Giorgina"}

    monkeypatch.setattr(ce, "lookup_places", fake_giorgina)
    info = ce.ContactEnricher().enrich(
        "\U0001d43a\U0001d456\U0001d45c\U0001d45f\U0001d454\U0001d456\U0001d45b\U0001d44e \U0001f499",
        48.838178, 2.491215, city="Nogent-sur-Marne", main_signal="création récente",
    )
    assert info.phone == "0184161110"
    assert info.website == "https://www.restaurant-giorgina.com/"
    assert info.review_count == 111

    # (b) Zone grise : overlap partiel (marco seul, aucun sous-ensemble) + 300
    # avis + création récente -> le détecteur rejette.
    def fake_grey(name, **k):
        return {"matched": True, "phone": "0102030405", "website": "https://marcopolo.fr",
                "review_count": 300, "match_basis": "geo", "display_name": "Marco Polo"}

    monkeypatch.setattr(ce, "lookup_places", fake_grey)
    info = ce.ContactEnricher().enrich(
        "Marco Del Caffé", None, None, city="Paris", main_signal="création récente",
    )
    assert info.phone is None and info.website is None
    assert info.review_count is None and info.match_basis is None

    # (c) SOKA/Bolkiri : zéro overlap -> toujours rejeté par le verrou d'identité.
    def fake_bolkiri(name, **k):
        return {"matched": True, "phone": "0183846538",
                "website": "https://restaurants.bolkiri.fr", "review_count": 218,
                "match_basis": "geo", "display_name": "Bolkiri Saint-Gratien"}

    monkeypatch.setattr(ce, "lookup_places", fake_bolkiri)
    info = ce.ContactEnricher().enrich(
        "SOKA FOOD", None, None, city="Saint-Gratien", main_signal="création récente",
    )
    assert info.phone is None and info.website is None and info.review_count is None


def test_fresh_review_conflict_detector():
    """Fix 2 (unité) — le détecteur rejette avis anciens vs création récente,
    par signal OU par date de début d'activité récente, et n'oppose rien aux
    faibles volumes / fiches établies. NB : l'appelant (enrich) ne le consulte
    qu'à identité FAIBLE (géo + nom fort -> exempté, cf. test ci-dessus)."""
    from app.ingestion.enrichment.contact_enricher import _fresh_review_conflict
    from datetime import date as _date, timedelta

    assert _fresh_review_conflict("création récente", None, 218) is True
    assert _fresh_review_conflict("ouverture prochaine", None, 100) is True
    assert _fresh_review_conflict("création récente", None, 12) is False   # peu d'avis
    assert _fresh_review_conflict("reprise", None, 900) is False           # signal non frais
    assert _fresh_review_conflict(None, _date.today() - timedelta(days=20), 300) is True
    assert _fresh_review_conflict(None, _date.today() - timedelta(days=400), 300) is False
    assert _fresh_review_conflict(None, _date.today() + timedelta(days=30), 300) is True  # pré-ouverture
    assert _fresh_review_conflict("création récente", None, None) is False  # avis inconnus


def test_external_url_excludes_non_sites():
    """Fix 2 — _external_url exclut PARTOUT les non-sites (bug de l'ancien
    `return url or None`) avec les URLs réelles citées par l'audit."""
    from app.ingestion.instagram import _external_url
    from app.ingestion.enrichment.url_filter import is_real_website, clean_website

    assert _external_url({"externalUrl": "https://linktr.ee/babelmontigny.restaurant"}) is None
    assert _external_url({"externalUrl": "https://maps.app.goo.gl/WTw4abc"}) is None
    assert _external_url({"externalUrl": "https://facebook.com/share/1CNRDUEGu2/"}) is None
    assert _external_url({"externalUrl": "https://instagram.com/foo"}) is None
    assert _external_url({"externalUrl": "https://bit.ly/xyz"}) is None
    assert _external_url({"externalUrl": "https://vietphe.fr"}) == "https://vietphe.fr"
    # Repli externalUrls : ignore l'agrégateur, retient le vrai site.
    assert _external_url({"externalUrl": "https://linktr.ee/x",
                          "externalUrls": [{"url": "https://linktr.ee/x"},
                                           {"url": "https://laperouse.com"}]}) == "https://laperouse.com"
    assert is_real_website("https://laperouse.com") is True
    assert is_real_website("https://linktr.ee/x") is False
    assert clean_website("  https://goo.gl/maps/x ") is None


def test_normalize_email_validates_format():
    """Fix 3 — un email n'est retenu que si le format est valide (minuscule) ;
    les 3 cas de l'audit (domaine nu id 319, numéros id 404/424) sont rejetés."""
    from app.services.contact_quality import normalize_email

    assert normalize_email("restaurant-giorgina.com") is None      # id 319 (sans @)
    assert normalize_email("01.43.54.10.12") is None               # id 404 (tél)
    assert normalize_email("01 43 06 83 35") is None               # id 424 (tél)
    assert normalize_email("Contact@Resto.FR") == "contact@resto.fr"
    assert normalize_email("marie.dupont@resto.fr") == "marie.dupont@resto.fr"
    assert normalize_email(None) is None
    assert normalize_email("") is None


def test_contact_enrich_one_rejects_malformed_email():
    """Fix 3 — la cascade contact ne stocke pas un email malformé (domaine nu /
    numéro de téléphone) : opp.email reste vide."""
    from app.models import Opportunity
    from app.ingestion.pipeline import _contact_enrich_one, ContactStats
    from app.ingestion.enrichment.contact_enricher import ContactInfo

    opp = Opportunity(
        establishment_name="Giorgina", establishment_type="restaurant", city="Nogent",
        address="1 Rue X, 94130 Nogent-sur-Marne", main_signal="création récente",
        secondary_signals=[], detection_date=date(2026, 6, 1), recommended_channel="email",
        source="instagram", source_ref="I1",
    )

    class Enr:
        def enrich(self, *a, **k):
            return ContactInfo(email="restaurant-giorgina.com")  # domaine nu

    class NoSirene:
        def lookup(self, siren): return None

    _contact_enrich_one(opp, Enr(), NoSirene(), ContactStats())
    assert opp.email is None
    assert opp.decision_maker_email is None


def test_contact_enrich_targets_retries_hot_segment():
    """Fix 4 — la sélection reprend les jamais-tentés ET les fiches CHAUDES
    tentées il y a >14 j auxquelles il manque email OU tél ; elle ignore les
    froides déjà tentées et les chaudes récentes ou déjà complètes."""
    from datetime import datetime, timedelta
    from sqlmodel import SQLModel, Session, create_engine
    from app.models import Opportunity
    from app.ingestion.pipeline import _contact_enrich_targets

    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    now = datetime(2026, 7, 10, 12, 0, 0)
    old = now - timedelta(days=20)
    recent = now - timedelta(days=3)

    def mk(ref, label, enriched_at, email=None, phone=None):
        return Opportunity(
            establishment_name=ref, establishment_type="restaurant", city="Paris",
            address="1 Rue X, 75011 Paris", estimated_timing="J-30",
            probable_needs=["luminaires"],
            main_signal="ouverture prochaine", secondary_signals=[],
            detection_date=date(2026, 6, 1), recommended_channel="email",
            source="instagram", source_ref=ref, lifecycle_label=label,
            contact_enriched_at=enriched_at, email=email, phone=phone,
        )

    with Session(engine) as s:
        s.add(mk("never", "opening_soon", None))                       # jamais tenté -> OUI
        s.add(mk("hot_stale", "opening_soon", old))                    # chaud, vieux, vide -> OUI
        s.add(mk("hot_recent", "just_opened", recent))                 # chaud mais récent -> NON
        s.add(mk("hot_complete", "renovation", old, email="a@b.fr", phone="0102030405"))  # complet -> NON
        s.add(mk("cold_stale", "established", old, phone="0102030405"))  # froid déjà tenté -> NON
        s.commit()

        refs = {o.source_ref for o in _contact_enrich_targets(s, "instagram", 100, now=now)}
        assert refs == {"never", "hot_stale"}
