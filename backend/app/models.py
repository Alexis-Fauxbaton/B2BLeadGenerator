"""Modèles SQLModel (tables SQLite)."""
from datetime import date, datetime
from typing import List, Optional

from sqlalchemy import Column, DateTime, func
from sqlalchemy.types import JSON
from sqlmodel import Field, Relationship, SQLModel


# --- Constantes métier (utilisées par l'app et la UI) -------------------------

ESTABLISHMENT_TYPES = [
    "restaurant",
    "café",
    "hôtel",
    "bar",
    "traiteur",
    "brasserie",
    "coffee shop",
]

SIGNAL_TYPES = [
    "ouverture prochaine",
    "reprise",
    "rénovation",
    "recrutement",
    "changement propriétaire",
    "nouveau point de vente",
    "travaux visibles",
    "annonce presse locale",
    "création récente",
    "expansion",
]

CHANNELS = ["instagram", "telephone", "email", "linkedin"]

STATUSES = [
    "non_contacte",
    "contacte",
    "relance",
    "interesse",
    "rdv",
    "gagne",
    "perdu",
]


# --- Tables -------------------------------------------------------------------


class Opportunity(SQLModel, table=True):
    __tablename__ = "opportunities"

    id: Optional[int] = Field(default=None, primary_key=True)
    establishment_name: str
    establishment_type: str
    city: str
    address: str

    main_signal: str
    secondary_signals: List[str] = Field(default_factory=list, sa_column=Column(JSON))

    detection_date: date
    # Date de début d'activité (BODACC dateCommencementActivite) : future =>
    # pas encore ouvert (pré-ouverture) ; passée => déjà ouvert. NULL si absent.
    activity_start_date: Optional[date] = None
    # Date d'origine du LOCAL (création du précédent exploitant d'une reprise) :
    # ancienne => lieu établi. NULL si pas une reprise / précédent non résolu.
    venue_origin_date: Optional[date] = None
    estimated_timing: str  # ex: "J-30", "J-60", "J-90"
    probable_needs: List[str] = Field(default_factory=list, sa_column=Column(JSON))
    decision_maker: Optional[str] = None
    # Tous les dirigeants déclarés (Président, DG, Gérant…) — pivots pour joindre
    # le décideur. `decision_maker` = le principal ; ceci = la liste complète.
    dirigeants: List[str] = Field(default_factory=list, sa_column=Column(JSON))

    opportunity_score: int = 0
    score_reason: str = ""
    recommended_channel: str = "telephone"
    channel_reason: str = ""

    proof_text: str = ""
    proof_url: str = ""

    # Provenance : "demo" (seed) ou nom du connecteur (ex: "bodacc").
    source: str = "demo"
    # Référence stable côté source (ex: id d'annonce BODACC) pour la dédup/upsert.
    source_ref: Optional[str] = Field(default=None, index=True)
    # Identifiant entreprise (SIREN) issu de l'enrichissement Sirene.
    siren: Optional[str] = Field(default=None, index=True)
    # Code NAF/APE (rempli par l'enrichissement). NULL = lead non encore
    # validé par Sirene -> cible de la passe de ré-enrichissement.
    naf: Optional[str] = Field(default=None, index=True)

    # Contact (enrichissement gratuit : OSM + scrape de site).
    phone: Optional[str] = None
    email: Optional[str] = None
    website: Optional[str] = None
    instagram: Optional[str] = None
    facebook: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    # Nb d'avis Google Places (proxy de fraîcheur : 0-20 = fenêtre d'aménagement
    # ouverte ; 200+ = établissement déjà installé). NULL = inconnu/non matché.
    review_count: Optional[int] = None
    # Confiance du contact ÉTABLISSEMENT (tél/email/site/insta du lieu) :
    # "haute" (match géo) | "moyenne" (nom+ville) | "basse". Pilote l'affichage.
    contact_confidence: Optional[str] = None
    # Bloc DÉCIDEUR : email nominatif de la personne + sa confiance.
    decision_maker_email: Optional[str] = None
    decision_maker_confidence: Optional[str] = None
    # NULL = enrichissement contact pas encore tenté (cible de la passe contact).
    contact_enriched_at: Optional[datetime] = None
    # Refresh : dernière vérification (heartbeat de fraîcheur) et date de fermeture
    # détectée (Sirene état != A) => stage "fermé".
    last_checked_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None

    status: str = "non_contacte"

    generated_instagram_dm: Optional[str] = None
    generated_email: Optional[str] = None
    generated_linkedin: Optional[str] = None
    generated_call_script: Optional[str] = None

    next_follow_up_date: Optional[date] = None

    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime, server_default=func.now()),
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime, server_default=func.now(), onupdate=func.now()),
    )

    signals: List["Signal"] = Relationship(
        back_populates="opportunity",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    contact_history: List["ContactHistory"] = Relationship(
        back_populates="opportunity",
        sa_relationship_kwargs={
            "cascade": "all, delete-orphan",
            "order_by": "ContactHistory.created_at.desc()",
        },
    )


class Signal(SQLModel, table=True):
    __tablename__ = "signals"

    id: Optional[int] = Field(default=None, primary_key=True)
    opportunity_id: int = Field(foreign_key="opportunities.id")
    signal_type: str
    source: str = ""
    source_url: str = ""
    signal_date: date
    confidence_score: float = 0.5
    raw_text: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)

    opportunity: Optional[Opportunity] = Relationship(back_populates="signals")


class ContactHistory(SQLModel, table=True):
    __tablename__ = "contact_history"

    id: Optional[int] = Field(default=None, primary_key=True)
    opportunity_id: int = Field(foreign_key="opportunities.id")
    channel: Optional[str] = None
    message: Optional[str] = None
    action_type: str  # ex: "message_genere", "statut_change", "relance_planifiee"
    status: Optional[str] = None
    note: Optional[str] = None
    contacted_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    opportunity: Optional[Opportunity] = Relationship(back_populates="contact_history")


class Settings(SQLModel, table=True):
    __tablename__ = "settings"

    id: Optional[int] = Field(default=None, primary_key=True)
    provider_name: str = "LumaPro"
    provider_offer: str = (
        "luminaires, mobilier et solutions d'ambiance pour restaurants, "
        "hôtels et commerces"
    )
    tone: str = "professionnel, direct, personnalisé"
    target_area: str = "Île-de-France"
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime, server_default=func.now(), onupdate=func.now()),
    )
