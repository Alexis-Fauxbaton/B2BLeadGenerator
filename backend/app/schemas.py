"""Schémas Pydantic pour les entrées/sorties de l'API."""
from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, computed_field, field_validator

from .services.lifecycle import freshness as _freshness
from .services.lifecycle import heat as _heat
from .services.lifecycle import lifecycle_stage as _stage


# --- Signal -------------------------------------------------------------------


class SignalRead(BaseModel):
    id: int
    signal_type: str
    source: str
    source_url: str
    signal_date: date
    confidence_score: float
    raw_text: str

    class Config:
        from_attributes = True


# --- Contact history ----------------------------------------------------------


class ContactHistoryRead(BaseModel):
    id: int
    channel: Optional[str]
    message: Optional[str]
    action_type: str
    status: Optional[str]
    note: Optional[str]
    contacted_at: Optional[datetime]
    created_at: datetime

    class Config:
        from_attributes = True


# --- Opportunity --------------------------------------------------------------


class OpportunityBase(BaseModel):
    establishment_name: str
    establishment_type: str
    city: str
    address: str
    main_signal: str
    secondary_signals: List[str] = []
    detection_date: date
    activity_start_date: Optional[date] = None
    venue_origin_date: Optional[date] = None
    estimated_timing: str
    probable_needs: List[str] = []
    decision_maker: Optional[str] = None
    dirigeants: List[str] = []
    opportunity_score: int = 0
    score_reason: str = ""
    recommended_channel: str = "telephone"
    channel_reason: str = ""
    proof_text: str = ""
    proof_url: str = ""
    status: str = "non_contacte"
    next_follow_up_date: Optional[date] = None


class OpportunityList(OpportunityBase):
    """Version allégée pour le tableau / pipeline."""

    id: int
    source: str = "demo"
    source_ref: Optional[str] = None
    lifecycle_label: Optional[str] = None
    siren: Optional[str] = None
    naf: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    website: Optional[str] = None
    instagram: Optional[str] = None
    facebook: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    review_count: Optional[int] = None
    contact_confidence: Optional[str] = None
    decision_maker_email: Optional[str] = None
    decision_maker_confidence: Optional[str] = None
    contact_enriched_at: Optional[datetime] = None
    last_checked_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    # Contacts multiples (profil Insta d'un groupe : autres adresses/emails).
    extra_addresses: List[str] = []
    extra_emails: List[str] = []
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

    # Colonnes JSON ajoutées après coup : NULL en base sur les anciennes lignes
    # -> on coerce en liste vide pour ne pas casser la sérialisation.
    @field_validator(
        "secondary_signals", "probable_needs", "dirigeants",
        "extra_addresses", "extra_emails", mode="before",
    )
    @classmethod
    def _coerce_none_list(cls, v):
        return v if v is not None else []

    # --- Cycle de vie : DÉRIVÉ à la sérialisation (pas stocké) ----------------
    @computed_field
    @property
    def lifecycle_stage(self) -> str:
        return _stage(
            self.main_signal, self.review_count, self.detection_date, date.today(),
            closed=self.closed_at is not None,
            activity_start_date=self.activity_start_date,
            venue_origin_date=self.venue_origin_date,
        )

    @computed_field
    @property
    def heat(self) -> str:
        return _heat(self.main_signal, self.detection_date, date.today())

    @computed_field
    @property
    def freshness(self) -> str:
        # Dernier "événement" connu : refresh (heartbeat) > enrichissement contact
        # > détection. Un refresh remet donc la fraîcheur à zéro.
        last_dt = self.last_checked_at or self.contact_enriched_at
        last = last_dt.date() if last_dt else self.detection_date
        return _freshness(last, date.today())


class OpportunityRead(OpportunityList):
    """Fiche détail complète."""

    generated_instagram_dm: Optional[str] = None
    generated_email: Optional[str] = None
    generated_linkedin: Optional[str] = None
    generated_call_script: Optional[str] = None
    signals: List[SignalRead] = []
    contact_history: List[ContactHistoryRead] = []


class OpportunityUpdate(BaseModel):
    """Mise à jour partielle d'une opportunité (PATCH)."""

    establishment_name: Optional[str] = None
    establishment_type: Optional[str] = None
    city: Optional[str] = None
    address: Optional[str] = None
    main_signal: Optional[str] = None
    secondary_signals: Optional[List[str]] = None
    estimated_timing: Optional[str] = None
    probable_needs: Optional[List[str]] = None
    decision_maker: Optional[str] = None
    proof_text: Optional[str] = None
    proof_url: Optional[str] = None
    next_follow_up_date: Optional[date] = None
    note: Optional[str] = None  # ajoutée à l'historique si fournie


class StatusUpdate(BaseModel):
    status: str
    note: Optional[str] = None
    next_follow_up_date: Optional[date] = None


# --- Messages -----------------------------------------------------------------


class GeneratedMessages(BaseModel):
    instagram_dm: str
    email: str
    linkedin: str
    call_script: str
    source: str  # "openai" ou "template"


# --- Settings -----------------------------------------------------------------


class SettingsRead(BaseModel):
    id: int
    provider_name: str
    provider_offer: str
    tone: str
    target_area: str
    updated_at: datetime

    class Config:
        from_attributes = True


class SettingsUpdate(BaseModel):
    provider_name: Optional[str] = None
    provider_offer: Optional[str] = None
    tone: Optional[str] = None
    target_area: Optional[str] = None


# --- Dashboard ----------------------------------------------------------------


class SignalBreakdown(BaseModel):
    label: str
    count: int


class StatusBreakdown(BaseModel):
    label: str
    count: int


class DashboardStats(BaseModel):
    total_opportunities: int
    hot_leads: int
    not_contacted: int
    follow_ups_due: int
    interested: int
    appointments: int
    won: int
    lost: int
    by_signal: List[SignalBreakdown]
    by_status: List[StatusBreakdown]
    hottest: List[OpportunityList]
