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
