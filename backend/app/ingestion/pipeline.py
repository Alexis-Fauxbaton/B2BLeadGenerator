"""Orchestration de l'ingestion (Transform + Load).

run_ingestion() : fetch via un connecteur -> classification CHR -> enrichissement
-> scoring & canal (services existants) -> dédup/upsert en SQLite.
Renvoie des statistiques exploitables par la CLI, l'endpoint et l'UI.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from typing import List, Optional

from sqlmodel import Session, delete, select

from ..database import engine, init_db
from ..models import ContactHistory, Opportunity, Signal
from ..services.channel_recommendation import recommend_channel
from ..services.contact_quality import (
    classify_email,
    decision_maker_confidence,
    establishment_confidence,
)
from ..services.scoring import compute_score
from ..services.segment import classify_segment
from .base import Connector, LeadCandidate
from .bodacc import BodaccConnector
from .chr_classifier import classify
from .enrichment.backfill import backfill_siren
from .enrichment.contact_enricher import ContactEnricher
from .enrichment.naf_classifier import classify_naf
from .enrichment.sirene import SireneEnricher
from .instagram import discover, scrape_hashtags

# Besoins probables par type d'établissement (alignés sur l'offre LumaPro).
NEEDS_BY_TYPE = {
    "restaurant": ["luminaires de salle", "mobilier de restaurant", "éclairage d'ambiance"],
    "café": ["luminaires de salle", "mobilier de terrasse"],
    "coffee shop": ["suspensions design", "éclairage comptoir", "mobilier"],
    "hôtel": ["éclairage lobby", "mobilier d'accueil", "appliques de couloir"],
    "bar": ["éclairage de bar", "mobilier", "enseigne lumineuse"],
    "brasserie": ["réagencement", "luminaires de salle", "mobilier de terrasse"],
    "traiteur": ["éclairage de boutique", "mobilier de présentation"],
}

TIMING_BY_SIGNAL = {
    "création récente": "J-30",
    "reprise": "J-60",
    "changement propriétaire": "J-60",
}

CONNECTORS = {
    "bodacc": BodaccConnector,
}


@dataclass
class IngestStats:
    source: str
    mode: str = "window"
    fetched: int = 0
    total_available: int = 0  # nb d'annonces correspondant au filtre côté BODACC
    truncated: bool = False  # True si total_available > fetched (fenêtre incomplète)
    enriched: int = 0
    chr_matched: int = 0
    created: int = 0
    updated: int = 0
    skipped_dupes: int = 0
    skipped_closed: int = 0
    errors: int = 0


@dataclass
class ReenrichStats:
    source: str = "bodacc"
    scanned: int = 0
    healed: int = 0
    still_missing: int = 0
    closed_now: int = 0
    removed_false_positive: int = 0
    errors: int = 0


@dataclass
class ContactStats:
    source: str = "bodacc"
    scanned: int = 0
    with_phone: int = 0
    with_email: int = 0
    with_website: int = 0
    with_instagram: int = 0
    with_any_priority: int = 0  # au moins email OU tel OU insta
    none: int = 0  # aucun contact trouvé
    errors: int = 0


@dataclass
class RefreshStats:
    source: str = "bodacc"
    checked: int = 0
    closed_now: int = 0  # fermetures nouvellement détectées (Sirene état != A)
    errors: int = 0


def get_connector(source: str) -> Connector:
    if source not in CONNECTORS:
        raise ValueError(f"Source inconnue : {source}. Disponibles : {list(CONNECTORS)}")
    return CONNECTORS[source]()


def run_ingestion(
    source: str = "bodacc",
    since_days: int = 90,
    limit: int = 100,
    departments: Optional[List[str]] = None,
    reset: bool = False,
    enrich: bool = True,
    since_date: Optional[date] = None,
    max_pages: int = 20,
    mode: str = "window",
    session: Optional[Session] = None,
) -> IngestStats:
    init_db()
    own_session = session is None
    session = session or Session(engine)
    stats = IngestStats(source=source, mode=mode)
    enricher = SireneEnricher() if enrich else None

    try:
        connector = get_connector(source)

        if reset:
            _reset_source(session, source)

        records = connector.fetch(
            since_days=since_days,
            limit=limit,
            departments=departments,
            since_date=since_date,
            max_pages=max_pages,
        )
        stats.fetched = len(records)
        stats.total_available = getattr(connector, "last_total_count", 0) or 0
        # Fenêtre tronquée : il y a plus d'annonces que ce qu'on a récupéré.
        stats.truncated = stats.total_available > stats.fetched

        candidates = connector.to_candidates(records)
        seen_refs: set[str] = set()

        for cand in candidates:
            try:
                _process_candidate(session, cand, stats, seen_refs, enricher)
            except Exception:
                stats.errors += 1
                session.rollback()

        session.commit()
    finally:
        if own_session:
            session.close()

    return stats


def run_incremental(
    source: str = "bodacc",
    overlap_days: int = 2,
    limit: int = 300,
    departments: Optional[List[str]] = None,
    enrich: bool = True,
    session: Optional[Session] = None,
) -> IngestStats:
    """Passe A — nouveaux leads depuis le dernier curseur (max detection_date),
    avec un chevauchement de sécurité. L'upsert évite les doublons."""
    own_session = session is None
    session = session or Session(engine)
    try:
        cursor = _source_cursor(session, source)
        since_date = (cursor - timedelta(days=overlap_days)) if cursor else None
        since_days = 90 if since_date is None else 0
        return run_ingestion(
            source=source,
            since_days=since_days,
            since_date=since_date,
            limit=limit,
            departments=departments,
            enrich=enrich,
            mode="incremental",
            session=session,
        )
    finally:
        if own_session:
            session.close()


def run_backfill(
    source: str = "bodacc",
    since_days: int = 120,
    limit: int = 5000,
    max_pages: int = 60,
    departments: Optional[List[str]] = None,
    enrich: bool = True,
    session: Optional[Session] = None,
) -> IngestStats:
    """Filet de sécurité — re-balaie une large fenêtre, indépendamment du
    curseur, pour combler les annonces jamais récupérées (rate-limit, crash)."""
    return run_ingestion(
        source=source,
        since_days=since_days,
        limit=limit,
        max_pages=max_pages,
        departments=departments,
        enrich=enrich,
        mode="backfill",
        session=session,
    )


def run_instagram(
    hashtags: Optional[List[str]] = None,
    limit: int = 40,
    session: Optional[Session] = None,
) -> IngestStats:
    """Source Instagram-first [PHASE 2] : Apify (hashtags) -> filtre CHR + IdF ->
    backfill SIREN (nom+ville) -> pipeline existant (Sirene/dirigeants/contact/
    score/cycle de vie). Upsert `source="instagram"`, dédup par handle."""
    init_db()
    own_session = session is None
    session = session or Session(engine)
    stats = IngestStats(source="instagram", mode="instagram")
    enricher = SireneEnricher()

    try:
        leads = discover(scrape_hashtags(hashtags, limit))
        stats.fetched = len(leads)
        seen_refs: set = set()
        for lead in leads:
            try:
                bf = backfill_siren(lead["name"], lead["city"]) or {}
                cand = LeadCandidate(
                    source="instagram",
                    source_ref=lead["handle"],
                    establishment_name=bf.get("enseigne") or lead["name"],
                    city=lead["city"],
                    main_signal="ouverture prochaine",
                    detection_date=date.today(),
                    classification_text=lead["name"],
                    establishment_type=lead["type"],  # pré-classé CHR à la découverte
                    instagram=lead["handle"],
                    siren=bf.get("siren"),
                    naf=bf.get("naf"),
                )
                _process_candidate(session, cand, stats, seen_refs, enricher)
            except Exception:
                stats.errors += 1
                session.rollback()
        session.commit()
    finally:
        if own_session:
            session.close()

    return stats


def _source_cursor(session: Session, source: str) -> Optional[date]:
    """Date de détection la plus récente déjà ingérée pour cette source."""
    rows = session.exec(
        select(Opportunity.detection_date).where(Opportunity.source == source)
    ).all()
    return max(rows) if rows else None


def _reset_source(session: Session, source: str) -> None:
    ids = [
        o.id
        for o in session.exec(select(Opportunity).where(Opportunity.source == source)).all()
    ]
    if ids:
        session.exec(delete(ContactHistory).where(ContactHistory.opportunity_id.in_(ids)))
        session.exec(delete(Signal).where(Signal.opportunity_id.in_(ids)))
        session.exec(delete(Opportunity).where(Opportunity.id.in_(ids)))
        session.commit()


def _process_candidate(
    session: Session,
    cand: LeadCandidate,
    stats: IngestStats,
    seen_refs: set,
    enricher: Optional[SireneEnricher] = None,
) -> None:
    # 1. Enrichissement Sirene (NAF, enseigne, adresse, état) si activé.
    if enricher is not None:
        enricher.enrich(cand)
        # Reprise : dater l'origine réelle du local via le précédent exploitant
        # (2e lookup Sirene) -> un vieux local repris = lieu "établi".
        if cand.previous_siren:
            prev = enricher.lookup(cand.previous_siren)
            cand.venue_origin_date = _ymd((prev or {}).get("date_creation"))
        if cand.enriched:
            stats.enriched += 1
        if cand.closed:
            stats.skipped_closed += 1
            return  # établissement fermé : on n'en fait pas un lead

    # 2. Classification CHR.
    # Si on a un NAF (enrichi), il fait AUTORITÉ : un NAF non-CHR écarte le lead,
    # même si la description BODACC contient des mots-clés CHR (cas des holdings
    # immobilières dont l'objet social mentionne "hôtel, restaurant").
    # Sans NAF (enrichissement indisponible), on retombe sur les mots-clés.
    text = " ".join(filter(None, [cand.classification_text, cand.establishment_name]))
    if cand.naf:
        etype = classify_naf(cand.naf, text)  # NAF fait autorité
    elif cand.establishment_type:
        etype = cand.establishment_type  # déjà validé CHR (ex. découverte Instagram)
    else:
        etype = classify(text)
    if not etype:
        return  # pas du CHR pertinent
    stats.chr_matched += 1

    # Dédup intra-batch
    if cand.source_ref in seen_refs:
        stats.skipped_dupes += 1
        return
    seen_refs.add(cand.source_ref)

    # 2. Enrichissement
    needs = NEEDS_BY_TYPE.get(etype, ["aménagement et ambiance"])
    timing = TIMING_BY_SIGNAL.get(cand.main_signal, "J-90")

    # 3. Canal + score (services existants)
    channel = recommend_channel(
        establishment_type=etype,
        main_signal=cand.main_signal,
        secondary_signals=cand.secondary_signals,
        decision_maker=cand.decision_maker,
        has_social_presence=False,
    )
    score = compute_score(
        main_signal=cand.main_signal,
        secondary_signals=cand.secondary_signals,
        detection_date=cand.detection_date,
        probable_needs=needs,
        decision_maker=cand.decision_maker,
        recommended_channel=channel.channel,
        segment=classify_segment(etype, cand.naf, cand.establishment_name),
    )

    # 4. Upsert (dédup persistante sur source + source_ref)
    existing = session.exec(
        select(Opportunity).where(
            Opportunity.source == cand.source,
            Opportunity.source_ref == cand.source_ref,
        )
    ).first()

    now = datetime.utcnow()

    if existing:
        existing.establishment_name = cand.establishment_name
        existing.establishment_type = etype
        existing.address = cand.address
        existing.siren = cand.siren
        existing.naf = cand.naf
        # Signaux & décideur : à rafraîchir aussi (sinon une amélioration du
        # parsing — origineFonds, administration — ne corrige jamais l'existant).
        existing.main_signal = cand.main_signal
        existing.secondary_signals = cand.secondary_signals
        existing.decision_maker = cand.decision_maker
        existing.dirigeants = cand.dirigeants
        if cand.instagram:
            existing.instagram = cand.instagram
        existing.activity_start_date = cand.activity_start_date
        existing.venue_origin_date = cand.venue_origin_date
        existing.estimated_timing = timing
        existing.probable_needs = needs
        if cand.latitude is not None:
            existing.latitude = cand.latitude
            existing.longitude = cand.longitude
        existing.proof_text = cand.proof_text
        existing.proof_url = cand.proof_url
        existing.detection_date = cand.detection_date
        existing.opportunity_score = score.score
        existing.score_reason = score.reason
        existing.recommended_channel = channel.channel
        existing.channel_reason = channel.reason
        existing.updated_at = now
        session.add(existing)
        stats.updated += 1
        return

    opp = Opportunity(
        establishment_name=cand.establishment_name,
        establishment_type=etype,
        city=cand.city,
        address=cand.address,
        main_signal=cand.main_signal,
        secondary_signals=cand.secondary_signals,
        detection_date=cand.detection_date,
        activity_start_date=cand.activity_start_date,
        venue_origin_date=cand.venue_origin_date,
        estimated_timing=timing,
        probable_needs=needs,
        decision_maker=cand.decision_maker,
        dirigeants=cand.dirigeants,
        opportunity_score=score.score,
        score_reason=score.reason,
        recommended_channel=channel.channel,
        channel_reason=channel.reason,
        proof_text=cand.proof_text,
        proof_url=cand.proof_url,
        source=cand.source,
        source_ref=cand.source_ref,
        siren=cand.siren,
        naf=cand.naf,
        instagram=cand.instagram,
        latitude=cand.latitude,
        longitude=cand.longitude,
        status="non_contacte",
        created_at=now,
        updated_at=now,
    )
    session.add(opp)
    session.flush()  # pour récupérer opp.id

    session.add(
        Signal(
            opportunity_id=opp.id,
            signal_type=cand.main_signal,
            source=f"BODACC ({cand.source})",
            source_url=cand.proof_url,
            signal_date=cand.detection_date,
            confidence_score=0.8,
            raw_text=cand.proof_text,
        )
    )
    session.add(
        ContactHistory(
            opportunity_id=opp.id,
            action_type="ingested",
            status="non_contacte",
            note=f"Lead importé automatiquement depuis {cand.source.upper()}.",
        )
    )
    stats.created += 1


def run_reenrich(
    source: str = "bodacc",
    limit: int = 1000,
    session: Optional[Session] = None,
) -> ReenrichStats:
    """Passe B — guérison. Re-tente l'enrichissement Sirene des leads non
    encore validés (naf IS NULL) via leur SIREN déjà stocké. NE TOUCHE PAS à
    BODACC : tout ce qui manquait vient de Sirene, et on a la clé (le SIREN)."""
    init_db()
    own_session = session is None
    session = session or Session(engine)
    stats = ReenrichStats(source=source)
    enricher = SireneEnricher()

    try:
        rows = session.exec(
            select(Opportunity).where(
                Opportunity.source == source,
                Opportunity.naf.is_(None),
                Opportunity.siren.is_not(None),
            )
        ).all()[:limit]

        for opp in rows:
            stats.scanned += 1
            try:
                verdict = _reenrich_one(opp, enricher, stats)
                if verdict == "false_positive":
                    _delete_opportunity(session, opp)
                else:
                    session.add(opp)
            except Exception:
                stats.errors += 1
                session.rollback()

        session.commit()
    finally:
        if own_session:
            session.close()

    return stats


def _delete_opportunity(session: Session, opp: Opportunity) -> None:
    session.exec(delete(ContactHistory).where(ContactHistory.opportunity_id == opp.id))
    session.exec(delete(Signal).where(Signal.opportunity_id == opp.id))
    session.delete(opp)


def _reenrich_one(opp: Opportunity, enricher: SireneEnricher, stats: ReenrichStats) -> str:
    """Ré-enrichit une opportunité en place. Renvoie un verdict :
    'healed' | 'missing' | 'closed' | 'false_positive'."""
    cand = LeadCandidate(
        source=opp.source,
        source_ref=opp.source_ref or "",
        establishment_name=opp.establishment_name,
        city=opp.city,
        address=opp.address,
        main_signal=opp.main_signal,
        detection_date=opp.detection_date,
        siren=opp.siren,
        classification_text=opp.establishment_name,
    )
    enricher.enrich(cand)

    if not cand.naf:
        stats.still_missing += 1
        return "missing"

    if cand.closed:
        stats.closed_now += 1
        opp.status = "perdu"
        opp.naf = cand.naf
        opp.updated_at = datetime.utcnow()
        return "closed"

    text = " ".join(filter(None, [cand.classification_text, cand.establishment_name]))
    etype = classify_naf(cand.naf, text)
    if not etype:
        # NAF désormais connu et NON-CHR : faux positif confirmé (n'existait que
        # par le repli mots-clés pendant une ingestion sans enrichissement).
        stats.removed_false_positive += 1
        return "false_positive"
    needs = NEEDS_BY_TYPE.get(etype, opp.probable_needs)

    channel = recommend_channel(
        establishment_type=etype,
        main_signal=opp.main_signal,
        secondary_signals=opp.secondary_signals,
        decision_maker=opp.decision_maker,
        has_social_presence=False,
    )
    score = compute_score(
        main_signal=opp.main_signal,
        secondary_signals=opp.secondary_signals,
        detection_date=opp.detection_date,
        probable_needs=needs,
        decision_maker=opp.decision_maker,
        recommended_channel=channel.channel,
        segment=classify_segment(etype, cand.naf, opp.establishment_name),
    )

    opp.naf = cand.naf
    opp.establishment_type = etype
    opp.establishment_name = cand.establishment_name
    opp.address = cand.address or opp.address
    opp.probable_needs = needs
    opp.recommended_channel = channel.channel
    opp.channel_reason = channel.reason
    opp.opportunity_score = score.score
    opp.score_reason = score.reason
    opp.updated_at = datetime.utcnow()
    stats.healed += 1
    return "healed"


def run_contact_enrich(
    source: str = "bodacc",
    limit: int = 500,
    reset: bool = False,
    session: Optional[Session] = None,
) -> ContactStats:
    """Passe contact — cible les leads dont l'enrichissement contact n'a pas
    encore été tenté (`contact_enriched_at IS NULL`). Ne remplit que les champs
    vides. Marque la tentative pour ne pas re-scanner indéfiniment.
    reset=True : ré-initialise le contact de la source (pour re-mesurer)."""
    init_db()
    own_session = session is None
    session = session or Session(engine)
    stats = ContactStats(source=source)
    enricher = ContactEnricher()
    sirene = SireneEnricher()

    try:
        if reset:
            for o in session.exec(select(Opportunity).where(Opportunity.source == source)).all():
                o.phone = o.email = o.website = o.instagram = o.facebook = None
                o.review_count = None
                o.contact_confidence = None
                o.decision_maker_email = None
                o.decision_maker_confidence = None
                o.contact_enriched_at = None
                session.add(o)
            session.commit()

        rows = session.exec(
            select(Opportunity).where(
                Opportunity.source == source,
                Opportunity.contact_enriched_at.is_(None),
            )
        ).all()[:limit]

        for opp in rows:
            stats.scanned += 1
            try:
                _contact_enrich_one(opp, enricher, sirene, stats)
                session.add(opp)
                session.commit()
            except Exception:
                stats.errors += 1
                session.rollback()
    finally:
        if own_session:
            session.close()

    return stats


def _contact_enrich_one(
    opp: Opportunity,
    enricher: ContactEnricher,
    sirene: SireneEnricher,
    stats: ContactStats,
) -> None:
    # Récupère les coords si absentes (via Sirene, par le SIREN stocké).
    lat, lon = opp.latitude, opp.longitude
    if (lat is None or lon is None) and opp.siren:
        data = sirene.lookup(opp.siren)
        if data:
            siege = data.get("siege") or {}
            lat = _coord(siege.get("latitude"))
            lon = _coord(siege.get("longitude"))
            if lat is not None:
                opp.latitude, opp.longitude = lat, lon

    postal = None
    m = re.search(r"\b\d{5}\b", opp.address or "")
    if m:
        postal = m.group(0)
    info = enricher.enrich(
        opp.establishment_name, lat, lon, website=opp.website, city=opp.city, postal=postal
    )

    # Contacts ÉTABLISSEMENT (ligne publique du lieu) — ne remplit que si vide.
    opp.phone = opp.phone or info.phone
    opp.website = opp.website or info.website
    opp.instagram = opp.instagram or info.instagram
    opp.facebook = opp.facebook or info.facebook

    # Email : router selon le niveau (role-based -> établissement ; nominatif ou
    # corroboré par le nom du dirigeant -> décideur).
    if info.email:
        if classify_email(info.email, opp.decision_maker) == "decideur":
            opp.decision_maker_email = opp.decision_maker_email or info.email
        else:
            opp.email = opp.email or info.email

    # Confiance (précision d'abord) : contact établissement fiable UNIQUEMENT si
    # match géo-confirmé ; sinon "à trouver". Une seule règle (pas de tambouille).
    has_estab = any([opp.phone, opp.email, opp.website, opp.instagram, opp.facebook])
    if has_estab:
        opp.contact_confidence = establishment_confidence(info.match_basis)
    opp.decision_maker_confidence = decision_maker_confidence(
        opp.decision_maker_email, opp.decision_maker
    )

    opp.contact_enriched_at = datetime.utcnow()

    # Fraîcheur Places (nb d'avis) : on ne FAIT CONFIANCE au nb d'avis que si le
    # match établissement est fiable (haute/moyenne). Sinon il vient
    # probablement d'un AUTRE établissement (cas BEAR YTD vs Bearsden, 424 avis)
    # -> on l'ignore (ni stocké, ni scoré, ni affiché). La nature création/reprise
    # vient du registre (origineFonds), pas des avis (retrait du misfire (b)).
    trusted_match = opp.contact_confidence == "haute"  # = géo-confirmé
    if info.review_count is not None and trusted_match:
        opp.review_count = info.review_count
        score = compute_score(
            main_signal=opp.main_signal,
            secondary_signals=opp.secondary_signals,
            detection_date=opp.detection_date,
            probable_needs=opp.probable_needs,
            decision_maker=opp.decision_maker,
            recommended_channel=opp.recommended_channel,
            segment=classify_segment(opp.establishment_type, opp.naf, opp.establishment_name),
            review_count=opp.review_count,
        )
        opp.opportunity_score = score.score
        opp.score_reason = score.reason
    else:
        # Match non fiable (ou pas d'avis) : on EFFACE tout nb d'avis trompeur
        # déjà stocké — il vient probablement d'un autre établissement.
        opp.review_count = None

    if opp.phone:
        stats.with_phone += 1
    if opp.email:
        stats.with_email += 1
    if opp.website:
        stats.with_website += 1
    if opp.instagram:
        stats.with_instagram += 1
    if opp.email or opp.phone or opp.instagram or opp.decision_maker_email:
        stats.with_any_priority += 1
    else:
        stats.none += 1


def run_refresh(
    source: str = "bodacc",
    limit: int = 1000,
    session: Optional[Session] = None,
) -> RefreshStats:
    """Passe REFRESH (gratuite, Sirene) — re-vérifie les leads ACTIFS :
    - détecte les FERMETURES (état administratif != A) -> closed_at + stage "fermé"
      + Signal "fermé" ;
    - pose un HEARTBEAT de fraîcheur (last_checked_at) -> remet la fraîcheur à zéro.
    Généralise `reenrich` à l'entretien dans le temps (cf. lead-lifecycle-design)."""
    init_db()
    own_session = session is None
    session = session or Session(engine)
    stats = RefreshStats(source=source)
    enricher = SireneEnricher()

    try:
        rows = session.exec(
            select(Opportunity).where(
                Opportunity.source == source,
                Opportunity.siren.is_not(None),
                Opportunity.closed_at.is_(None),
                Opportunity.status.notin_(["gagne", "perdu"]),
            )
        ).all()[:limit]

        for opp in rows:
            stats.checked += 1
            try:
                _refresh_one(session, opp, enricher, stats)
                session.add(opp)
                session.commit()
            except Exception:
                stats.errors += 1
                session.rollback()
    finally:
        if own_session:
            session.close()

    return stats


def _refresh_one(session: Session, opp: Opportunity, enricher: SireneEnricher, stats: RefreshStats) -> None:
    now = datetime.utcnow()
    opp.last_checked_at = now  # heartbeat -> fraîcheur remise à zéro
    data = enricher.lookup(opp.siren)
    if data:
        siege = data.get("siege") or {}
        etat = data.get("etat_administratif") or siege.get("etat_administratif")
        if etat and etat.upper() != "A" and opp.closed_at is None:
            opp.closed_at = now
            opp.status = "perdu"
            stats.closed_now += 1
            session.add(
                Signal(
                    opportunity_id=opp.id,
                    signal_type="fermé",
                    source="Sirene (refresh)",
                    signal_date=date.today(),
                    confidence_score=0.9,
                    raw_text=f"Établissement fermé (état administratif {etat}).",
                )
            )
    opp.updated_at = now


def _coord(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ymd(value):
    """Parse 'YYYY-MM-DD' -> date, ou None."""
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def stats_to_dict(stats) -> dict:
    return asdict(stats)
