"""Peuplement de la base avec des données de démonstration.

Usage :
    python -m app.seed

Le script supprime les données existantes (Opportunity / Signal / ContactHistory)
puis recrée les opportunités à partir de app.services.demo_data, en calculant
le score et le canal recommandé via les services dédiés.
"""
from datetime import date, datetime, timedelta

from sqlmodel import Session, delete, select

from .database import engine, init_db
from .models import ContactHistory, Opportunity, Settings, Signal
from .services.channel_recommendation import recommend_channel
from .services.demo_data import DEMO_LEADS
from .services.scoring import compute_score
from .services.segment import classify_segment

# Source affichée pour les signaux détaillés, selon le type de signal.
SIGNAL_SOURCES = {
    "ouverture prochaine": "Veille locale / réseaux sociaux",
    "création récente": "Registre des entreprises (simulé)",
    "reprise": "Annonces légales (simulé)",
    "changement propriétaire": "Annonces légales (simulé)",
    "rénovation": "Veille terrain / presse locale",
    "travaux visibles": "Veille terrain",
    "recrutement": "Offres d'emploi (simulé)",
    "nouveau point de vente": "Communication d'enseigne",
    "annonce presse locale": "Presse locale",
    "expansion": "Communication d'entreprise",
    "compte instagram récent": "Réseaux sociaux",
}


def ensure_settings(session: Session) -> None:
    existing = session.exec(select(Settings)).first()
    if not existing:
        session.add(Settings())
        session.commit()


def clear_opportunities(session: Session) -> None:
    """Ne supprime que les données de démonstration (source="demo"),
    afin de préserver les leads réels importés (ex: BODACC)."""
    demo_ids = [
        o.id
        for o in session.exec(
            select(Opportunity).where(Opportunity.source == "demo")
        ).all()
    ]
    if demo_ids:
        session.exec(delete(ContactHistory).where(ContactHistory.opportunity_id.in_(demo_ids)))
        session.exec(delete(Signal).where(Signal.opportunity_id.in_(demo_ids)))
        session.exec(delete(Opportunity).where(Opportunity.id.in_(demo_ids)))
    session.commit()


def seed() -> int:
    init_db()
    today = date.today()
    now = datetime.utcnow()

    with Session(engine) as session:
        ensure_settings(session)
        clear_opportunities(session)

        count = 0
        for lead in DEMO_LEADS:
            detection_date = today - timedelta(days=lead.get("days_ago", 10))

            channel = recommend_channel(
                establishment_type=lead["establishment_type"],
                main_signal=lead["main_signal"],
                secondary_signals=lead.get("secondary_signals", []),
                decision_maker=lead.get("decision_maker"),
                has_social_presence=lead.get("has_social", False),
            )
            score = compute_score(
                main_signal=lead["main_signal"],
                secondary_signals=lead.get("secondary_signals", []),
                detection_date=detection_date,
                probable_needs=lead.get("probable_needs", []),
                decision_maker=lead.get("decision_maker"),
                recommended_channel=channel.channel,
                today=today,
                segment=classify_segment(lead["establishment_type"]),
            )

            next_follow_up = None
            if "next_follow_up_in" in lead:
                next_follow_up = today + timedelta(days=lead["next_follow_up_in"])

            opp = Opportunity(
                establishment_name=lead["establishment_name"],
                establishment_type=lead["establishment_type"],
                city=lead["city"],
                address=lead["address"],
                main_signal=lead["main_signal"],
                secondary_signals=lead.get("secondary_signals", []),
                detection_date=detection_date,
                estimated_timing=lead["estimated_timing"],
                probable_needs=lead.get("probable_needs", []),
                decision_maker=lead.get("decision_maker"),
                opportunity_score=score.score,
                score_reason=score.reason,
                recommended_channel=channel.channel,
                channel_reason=channel.reason,
                proof_text=lead.get("proof_text", ""),
                proof_url=lead.get("proof_url", ""),
                status=lead.get("status", "non_contacte"),
                next_follow_up_date=next_follow_up,
                created_at=now,
                updated_at=now,
            )
            session.add(opp)
            session.commit()
            session.refresh(opp)

            # Signaux détaillés (preuves) : principal + secondaires.
            all_types = [lead["main_signal"], *lead.get("secondary_signals", [])]
            for i, signal_type in enumerate(all_types):
                session.add(
                    Signal(
                        opportunity_id=opp.id,
                        signal_type=signal_type,
                        source=SIGNAL_SOURCES.get(signal_type, "Veille (simulé)"),
                        source_url=lead.get("proof_url", ""),
                        signal_date=detection_date - timedelta(days=i),
                        confidence_score=0.9 if i == 0 else 0.6,
                        raw_text=lead.get("proof_text", "") if i == 0 else f"Signal secondaire : {signal_type}.",
                    )
                )

            # Historique initial selon le statut.
            if opp.status != "non_contacte":
                session.add(
                    ContactHistory(
                        opportunity_id=opp.id,
                        channel=channel.channel,
                        action_type="statut_change",
                        status=opp.status,
                        note="Statut initial (donnée de démonstration).",
                        contacted_at=now,
                    )
                )

            session.commit()
            count += 1

        print(f"[OK] Seed termine : {count} opportunites creees.")
        return count


if __name__ == "__main__":
    seed()
