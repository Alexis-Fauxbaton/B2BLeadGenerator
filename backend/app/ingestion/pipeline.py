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

from sqlalchemy import and_, or_
from sqlmodel import Session, delete, select

from ..database import engine, init_db
from ..models import ContactHistory, Opportunity, Signal
from ..services.channel_recommendation import recommend_channel
from ..services.contact_quality import (
    classify_email,
    decision_maker_confidence,
    establishment_confidence,
    normalize_email,
)
from ..services.scoring import compute_score
from ..services.segment import classify_segment
from .base import Connector, LeadCandidate
from .bodacc import BodaccConnector
from .chr_classifier import classify
from .sirene_delta import SireneDeltaConnector
from .enrichment.contact_enricher import ContactEnricher
from .enrichment.naf_classifier import classify_naf
from .enrichment.sirene import SireneEnricher
from .enrichment import siret_matcher
from .enrichment.siret_matcher import MatchResult, match as match_siret
from .instagram import discover, scrape_hashtags, scrape_profiles, classify_profiles
from . import verdict_cache

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
    "rénovation": "J-30",
    "reprise": "J-60",
    "changement propriétaire": "J-60",
}

# Signal NEUTRE des leads « en base » (établis, chaînes, indéterminés du funnel
# Insta) : présent dans SIGNAL_TYPES, membre d'AUCUNE famille de scoring
# (services/scoring.py) -> aucun bonus de nature, score naturellement bas (les
# ouvertures restent en tête du tri). On n'invente AUCUN signal d'achat pour un
# établissement qui n'en émet pas.
NEUTRAL_SIGNAL = "établissement en activité"
# Libellé harmonisé avec le delta-Sirene (sirene_delta.py) : une marque qui ouvre
# un nouveau lieu = extension multi-sites (signal secondaire, non chaud).
MULTISITE_SIGNAL = "extension multi-sites"

# Routage label de cycle de vie -> (main_signal, secondary_signals, lifecycle_label).
# not_venue/noise ABSENTS -> aucun lead (verdict caché uniquement). unknown =
# lead « en base » NEUTRE (plus jamais déguisé en « ouverture prochaine »).
LABEL_ROUTING = {
    "opening_soon":    ("ouverture prochaine", [],                 "opening_soon"),
    "just_opened":     ("création récente",    [],                 "just_opened"),
    # renovation : établi EN TRAVAUX = segment CHAUD (fenêtre d'aménagement
    # ouverte). main_signal 'rénovation' -> famille RENOVATION du scoring
    # (services/scoring.py), bonus de nature -> lead priorisé, jamais caché.
    "renovation":      ("rénovation",          [],                 "renovation"),
    "established":     (NEUTRAL_SIGNAL,         [],                 "established"),
    "chain_multisite": (NEUTRAL_SIGNAL,         [MULTISITE_SIGNAL], "chain_multisite"),
    "unknown":         (NEUTRAL_SIGNAL,         [],                 "unknown"),
}

CONNECTORS = {
    "bodacc": BodaccConnector,
    "sirene": SireneDeltaConnector,
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


def _match_result(lead: dict) -> Optional["MatchResult"]:
    """Lead Insta -> MatchResult brut via le matcher, ou None (fail-soft).
    Contexte de l'arbitre : bio_snippet (profil) + caption de découverte."""
    parts = [p for p in (lead.get("bio_snippet"), lead.get("caption")) if p]
    context = " | post: ".join(parts)[:600] if parts else None
    m = re.search(r"\b(\d{5})\b", lead.get("address") or "")
    return match_siret(
        name=lead.get("name") or "",
        city=lead.get("city"),
        postal=m.group(1) if m else None,
        address=lead.get("address"),
        context=context,
    )


def _match_lead(lead: dict) -> dict:
    """Lead Insta -> {siren, naf, enseigne, siret, method, confidence}, ou {}."""
    got = _match_result(lead)
    if got is None:
        return {}
    return {
        "siren": got.siren, "naf": got.naf, "enseigne": got.enseigne,
        "siret": got.siret, "method": got.method, "confidence": got.confidence,
    }


def _instagram_proof(c: dict, prof: dict) -> tuple[Optional[str], str]:
    """Construit (proof_text, proof_url) du lead Instagram v2 : la preuve
    concrète du verdict (bio + caption de découverte + label/confiance du
    juge), affichée telle quelle par la carte « Signal & preuve » de l'UI.
    `prof` fait autorité pour la bio (couvre aussi les verdicts tranchés par
    un garde déterministe, où classify_profiles ne pose pas `bio_snippet`).
    Ne renvoie jamais une chaîne vide : None si vraiment aucune évidence."""
    handle = c["handle"]
    proof_url = f"https://instagram.com/{handle}"
    bio = (prof.get("biography") or c.get("bio_snippet") or "").strip()[:150]
    caption = (c.get("caption") or "").strip()[:150]
    parts = []
    if bio:
        parts.append(f"Bio : «{bio}»")
    if caption:
        parts.append(f"Post : «{caption}»")
    proof_text = " — ".join(parts)
    label = c.get("label")
    if label:
        confidence = c.get("confidence") or "inconnue"
        suffix = f"(label {label}, confiance {confidence})"
        proof_text = f"{proof_text} {suffix}".strip() if proof_text else suffix
    return (proof_text or None), proof_url


def run_instagram(
    hashtags: Optional[List[str]] = None,
    limit: int = 40,
    session: Optional[Session] = None,
    posts: Optional[List[dict]] = None,
) -> IngestStats:
    """Source Instagram-first [PHASE 2] : Apify (hashtags) -> filtre CHR + IdF ->
    matching SIREN (nom/adresse/arbitre, siret_matcher) -> pipeline existant
    (Sirene/dirigeants/contact/score/cycle de vie). Upsert `source="instagram"`,
    dédup par handle."""
    init_db()
    own_session = session is None
    session = session or Session(engine)
    stats = IngestStats(source="instagram", mode="instagram")
    enricher = SireneEnricher()

    try:
        raw_posts = posts if posts is not None else scrape_hashtags(hashtags, limit)
        candidates = discover(raw_posts)
        # Filtre cache : ne scrape/juge que les handles dus (recall-safe : un
        # handle absent du cache ou hors fenêtre passe ; les not_venue/established
        # récents sont sautés -> économie de scrape).
        # COÛT ASSUMÉ [revue finale] : le pré-filtre caption (ancien juge groupé)
        # a été retiré ; au cold-start (cache vide) et pour tout nouveau handle on
        # paie donc un profile scrape Apify (+ un passage matcher, cf.
        # classify_profiles) PAR candidate CHR+IdF, pas seulement pour les
        # ouvertures. Le cache n'amortit que les runs répétés, pas le 1er passage.
        due = [c for c in candidates if verdict_cache.should_rejudge(session, c["handle"])]
        profiles = scrape_profiles([c["handle"] for c in due]) if due else {}
        today = date.today()
        labeled = classify_profiles(due, profiles, match_fn=_match_result, today=today)
        stats.fetched = len(labeled)
        seen_refs: set = set()
        for c in labeled:
            prof = profiles.get(c["handle"].lower()) or {}
            has_data = bool(prof.get("latestPosts") or prof.get("postsCount") is not None)
            # CACHE — n'écrire un verdict QUE s'il repose sur une vraie évidence de
            # profil ET un vrai jugement. Un 'unknown' de confiance 'basse' (scrape
            # échoué -> prof vide, clé LLM absente, ou juge en erreur) N'EST PAS
            # caché : le handle reste « dû » et sera re-scrapé/re-jugé au prochain
            # run plutôt qu'endormi 2 mois. Sinon une seule panne transitoire
            # suffirait à endormir tout le set découvert et à casser le recall.
            cacheable = has_data and not (
                c["label"] == "unknown" and (c.get("confidence") or "basse") == "basse"
            )
            # ROUTAGE brique 3bis : TOUT label devient un lead SAUF not_venue/noise
            # (absents de LABEL_ROUTING -> cache seul). Les ouvertures gardent leur
            # signal d'achat ; établis/chaînes/unknown reçoivent un signal NEUTRE
            # (score naturellement bas) + le label de cycle de vie persisté.
            routing = LABEL_ROUTING.get(c["label"])
            try:
                # Verdict de cache ET lead COMMITTÉS ENSEMBLE par candidat (même
                # unité transactionnelle) : si la création du lead échoue plus bas
                # (enrich réseau, classify, scoring), le rollback annule AUSSI le
                # verdict -> should_rejudge reste vrai et le handle est re-jugé au
                # prochain run (sa fiche « en base » n'est pas condamnée à ne
                # jamais exister pendant la fenêtre de revisite). L'isolation
                # inter-candidats est préservée : le commit par candidat protège
                # les verdicts/leads déjà réussis du lot.
                if cacheable:
                    verdict_cache.upsert(session, c["handle"], c["label"],
                                         c.get("confidence"), prof, today=today)
                if routing is not None:
                    main_signal, secondary_signals, lifecycle_label = routing
                    m = c.get("_match")
                    proof_text, proof_url = _instagram_proof(c, prof)
                    cand = LeadCandidate(
                        source="instagram",
                        source_ref=c["handle"],
                        establishment_name=(m.enseigne if (m and m.enseigne) else c["name"]),
                        city=c["city"],
                        address=c.get("address", ""),
                        email=c.get("email"),
                        website=c.get("website"),
                        extra_addresses=c.get("extra_addresses", []),
                        extra_emails=c.get("extra_emails", []),
                        main_signal=main_signal,
                        secondary_signals=list(secondary_signals),
                        lifecycle_label=lifecycle_label,
                        detection_date=today,
                        classification_text=c["name"],
                        establishment_type=c["type"],  # pré-classé CHR à la découverte
                        instagram=c["handle"],
                        siren=(m.siren if m else None),
                        naf=(m.naf if m else None),
                        siret=(m.siret if m else None),
                        siren_match_method=(m.method if m else None),
                        siren_match_confidence=(m.confidence if m else None),
                        proof_text=proof_text or "",
                        proof_url=proof_url,
                    )
                    _process_candidate(session, cand, stats, seen_refs, enricher)
                # Commit unique (verdict + lead) PAR candidat : un échec ultérieur
                # ne défait ni ce couple ni les précédents (isolation du lot).
                session.commit()
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


CORROBORATION_TAG = "corroboré registre × instagram"

# Libellé Signal par source — réutilisé par la fusion cross-source et par le
# garde-fou anti-duplication (Signal déjà posé pour cette provenance).
SOURCE_LABELS = {"bodacc": "BODACC", "instagram": "Instagram", "sirene": "Sirene (délta)"}


def _merge_corroboration(session: Session, opp: Opportunity, cand: LeadCandidate) -> None:
    """Fusionne un candidat cross-source dans la fiche existante (ne remplit
    que les trous, n'ecrase rien), tague la corroboration (si Instagram est
    impliqué) et rescore."""
    opp.siret = opp.siret or cand.siret
    opp.address = opp.address or cand.address
    opp.email = opp.email or cand.email
    opp.website = opp.website or cand.website
    opp.instagram = opp.instagram or cand.instagram
    opp.naf = opp.naf or cand.naf
    opp.lifecycle_label = opp.lifecycle_label or cand.lifecycle_label
    opp.activity_start_date = opp.activity_start_date or cand.activity_start_date
    # BODACC/Sirene apportent chacun une valeur propre (dirigeants, preuve) :
    # on la garde même quand la fusion n'est pas de nature "corroboré instagram".
    opp.decision_maker = opp.decision_maker or cand.decision_maker
    opp.dirigeants = opp.dirigeants or cand.dirigeants
    opp.proof_text = opp.proof_text or cand.proof_text
    opp.proof_url = opp.proof_url or cand.proof_url
    sigs = list(opp.secondary_signals or [])
    # Le tag "corroboré registre × instagram" n'a de sens que si Instagram est
    # l'une des deux sources ; une fusion BODACC×Sirene (deux registres) n'est
    # pas une corroboration "registre × instagram" — et ce libellé est
    # score-bearing (famille de signal inconnue = +1 en scoring).
    if "instagram" in (opp.source, cand.source) and CORROBORATION_TAG not in sigs:
        sigs.append(CORROBORATION_TAG)
    opp.secondary_signals = sigs
    channel = recommend_channel(
        establishment_type=opp.establishment_type,
        main_signal=opp.main_signal,
        secondary_signals=sigs,
        decision_maker=opp.decision_maker,
        has_social_presence=bool(opp.instagram),
    )
    score = compute_score(
        main_signal=opp.main_signal,
        secondary_signals=sigs,
        detection_date=opp.detection_date,
        probable_needs=opp.probable_needs,
        decision_maker=opp.decision_maker,
        recommended_channel=channel.channel,
        segment=classify_segment(opp.establishment_type, opp.naf, opp.establishment_name),
        review_count=opp.review_count,
    )
    opp.opportunity_score = score.score
    opp.score_reason = score.reason
    opp.recommended_channel = channel.channel
    opp.channel_reason = channel.reason
    opp.updated_at = datetime.utcnow()
    session.add(opp)
    session.add(Signal(
        opportunity_id=opp.id,
        signal_type=cand.main_signal,
        source=SOURCE_LABELS.get(cand.source, cand.source),
        source_url=cand.proof_url,
        signal_date=cand.detection_date,
        confidence_score=0.9,
        raw_text=cand.proof_text,
    ))


def _process_candidate(
    session: Session,
    cand: LeadCandidate,
    stats: IngestStats,
    seen_refs: set,
    enricher: Optional[SireneEnricher] = None,
) -> None:
    # 1. Enrichissement Sirene (NAF, enseigne, adresse, état) si activé.
    # Source "sirene" exclue : données INSEE déjà autoritatives et fraîches
    # (l'enrichisseur écraserait l'adresse d'un établissement secondaire par
    # celle du siège — extension multi-sites) ; la passe `refresh` gère les
    # fermetures pour cette source, sans le coût réseau (~0,5 s/candidat/run).
    if enricher is not None and cand.source != "sirene":
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

    # FUSION PAR SIREN [BRIQUE 2] : le meme etablissement vu par une AUTRE
    # source ne cree pas de doublon — il CORROBORE (registre x instagram =
    # quasi-certitude d'ouverture). La fiche d'origine est conservee, la
    # provenance entrante est journalisee en Signal.
    corroborated = None
    if existing is None and cand.siren:
        corroborated = session.exec(
            select(Opportunity).where(
                Opportunity.siren == cand.siren,
                Opportunity.source != cand.source,
            )
        ).first()

    if (
        corroborated is not None
        and corroborated.siret
        and cand.siret
        and corroborated.siret != cand.siret
    ):
        # Même SIREN mais SIRET différent : un autre établissement de la même
        # entreprise (chaînes multi-sites), pas le même site -> pas de fusion,
        # c'est un lead à part entière (retombe sur la création normale).
        corroborated = None

    if corroborated is not None:
        # Anti-duplication [revue finale] : si un Signal existe déjà pour
        # cette fiche avec la même provenance (source) ET le même type de
        # signal, la fusion a déjà eu lieu lors d'un run précédent (ex. delta
        # Sirene quotidien qui revoit le même candidat) — ne rien refaire (pas
        # de re-remplissage, pas de rescore, pas de nouveau Signal, pas de
        # stats.updated) pour éviter d'empiler des Signal dupliqués.
        label = SOURCE_LABELS.get(cand.source, cand.source)
        already = session.exec(
            select(Signal).where(
                Signal.opportunity_id == corroborated.id,
                Signal.source == label,
                Signal.signal_type == cand.main_signal,
            )
        ).first()
        if already is not None:
            return
        _merge_corroboration(session, corroborated, cand)
        stats.updated += 1
        return

    if existing:
        sigs = list(cand.secondary_signals or [])
        if CORROBORATION_TAG in (existing.secondary_signals or []) and CORROBORATION_TAG not in sigs:
            # Ne pas effacer le tag de corroboration posé par une fusion
            # précédente : cet upsert même-source ne le sait pas, il faut le
            # préserver explicitement (sinon écrasé par cand.secondary_signals).
            sigs.append(CORROBORATION_TAG)
        channel = recommend_channel(
            establishment_type=etype,
            main_signal=cand.main_signal,
            secondary_signals=sigs,
            decision_maker=cand.decision_maker,
            has_social_presence=False,
        )
        score = compute_score(
            main_signal=cand.main_signal,
            secondary_signals=sigs,
            detection_date=cand.detection_date,
            probable_needs=needs,
            decision_maker=cand.decision_maker,
            recommended_channel=channel.channel,
            segment=classify_segment(etype, cand.naf, cand.establishment_name),
        )
        existing.establishment_name = cand.establishment_name
        existing.establishment_type = etype
        existing.address = cand.address
        existing.siren = cand.siren
        existing.naf = cand.naf
        existing.siret = cand.siret or existing.siret
        existing.siren_match_method = cand.siren_match_method or existing.siren_match_method
        existing.siren_match_confidence = cand.siren_match_confidence or existing.siren_match_confidence
        # Signaux & décideur : à rafraîchir aussi (sinon une amélioration du
        # parsing — origineFonds, administration — ne corrige jamais l'existant).
        existing.main_signal = cand.main_signal
        existing.secondary_signals = sigs
        existing.decision_maker = cand.decision_maker
        existing.dirigeants = cand.dirigeants
        if cand.instagram:
            existing.instagram = cand.instagram
        # Contact enrichi à la source (profil Insta) : email/site/adresses multiples.
        if cand.email:
            existing.email = cand.email
        if cand.website:
            existing.website = cand.website
        if cand.extra_addresses:
            existing.extra_addresses = cand.extra_addresses
        if cand.extra_emails:
            existing.extra_emails = cand.extra_emails
        if cand.source == "instagram" and (cand.email or cand.website or cand.address):
            existing.contact_confidence = "haute"
        # Rafraîchir le label de cycle de vie (un opening peut devenir established
        # à un run ultérieur, ou l'inverse) — ne pas écraser par None (BODACC).
        existing.lifecycle_label = cand.lifecycle_label or existing.lifecycle_label
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
        siret=cand.siret,
        siren_match_method=cand.siren_match_method,
        siren_match_confidence=cand.siren_match_confidence,
        instagram=cand.instagram,
        lifecycle_label=cand.lifecycle_label,
        email=cand.email,
        website=cand.website,
        extra_addresses=cand.extra_addresses,
        extra_emails=cand.extra_emails,
        # Contact issu du profil Insta (adresse business / email déclarés) = fiable.
        contact_confidence=(
            "haute" if cand.source == "instagram" and (cand.email or cand.website or cand.address)
            else None
        ),
        latitude=cand.latitude,
        longitude=cand.longitude,
        status="non_contacte",
        created_at=now,
        updated_at=now,
    )
    session.add(opp)
    session.flush()  # pour récupérer opp.id

    source_label = {
        "bodacc": "BODACC", "instagram": "Instagram", "demo": "Démo",
    }.get(cand.source, cand.source)
    session.add(
        Signal(
            opportunity_id=opp.id,
            signal_type=cand.main_signal,
            source=source_label,
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


# Segment CHAUD (fenêtre d'aménagement ouverte MAINTENANT) : la passe contact
# doit y REVENIR. Un établissement trop récent pour Google Places aujourd'hui y
# sera dans 3 semaines -> on re-tente les fiches chaudes anciennes de >14 j
# auxquelles il manque encore email OU téléphone.
HOT_LABELS = ("opening_soon", "just_opened", "renovation")
CONTACT_RETRY_DAYS = 14


def _contact_enrich_targets(
    session: Session, source: str, limit: int, now: Optional[datetime] = None
):
    """Fiches à (ré)enrichir : jamais tentées (`contact_enriched_at IS NULL`) OU
    segment CHAUD tenté il y a plus de 14 j et à qui il manque email OU tél.
    Extrait pur (testable sans réseau) — la sélection est le cœur du Fix 4."""
    now = now or datetime.utcnow()
    stale_cutoff = now - timedelta(days=CONTACT_RETRY_DAYS)
    return session.exec(
        select(Opportunity).where(
            Opportunity.source == source,
            or_(
                Opportunity.contact_enriched_at.is_(None),
                and_(
                    Opportunity.lifecycle_label.in_(HOT_LABELS),
                    Opportunity.contact_enriched_at < stale_cutoff,
                    or_(Opportunity.email.is_(None), Opportunity.phone.is_(None)),
                ),
            ),
        )
    ).all()[:limit]


def run_contact_enrich(
    source: str = "bodacc",
    limit: int = 500,
    reset: bool = False,
    session: Optional[Session] = None,
) -> ContactStats:
    """Passe contact — cible les leads jamais tentés (`contact_enriched_at IS
    NULL`) ET les fiches du segment CHAUD dont la tentative date de plus de 14 j
    s'il leur manque encore email OU téléphone (un opening trop récent pour
    Places y sera dans 3 semaines : la passe doit revenir). Ne remplit que les
    champs vides. reset=True : ré-initialise le contact de la source (re-mesure)."""
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

        rows = _contact_enrich_targets(session, source, limit)

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

    # Fiches Instagram (lat/lon NULL, souvent sans SIREN) : géocode l'adresse via
    # BAN pour DONNER SA CHANCE au verrou géo de Places (Fix 1). Fail-soft, on
    # ne retient que les géocodages fiables (score BAN >= 0.6, cf. siret_matcher).
    if (lat is None or lon is None) and opp.address:
        coords = siret_matcher.geocode(opp.address, siret_matcher._http_get)
        if coords:
            lat, lon = coords
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

    # Email : VALIDER le format d'abord (Fix 3 — un champ vide vaut mieux qu'un
    # domaine nu ou un numéro de téléphone), puis router selon le niveau
    # (role-based -> établissement ; nominatif ou corroboré dirigeant -> décideur).
    email = normalize_email(info.email)
    if email:
        if classify_email(email, opp.decision_maker) == "decideur":
            opp.decision_maker_email = opp.decision_maker_email or email
        else:
            opp.email = opp.email or email

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
