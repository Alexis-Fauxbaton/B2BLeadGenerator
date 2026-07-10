"""Interface commune des connecteurs d'ingestion (le "Extract" de l'ETL).

Un connecteur sait :
  - récupérer des enregistrements bruts depuis une source (fetch),
  - les transformer en LeadCandidate normalisés (to_candidates).

Le reste de la chaîne (classification CHR, enrichissement, scoring, dédup,
écriture en base) est mutualisé dans pipeline.py, quel que soit le connecteur.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional


@dataclass
class LeadCandidate:
    """Lead normalisé, indépendant de la source.

    `establishment_type` peut être None à la sortie du connecteur : c'est le
    classifier CHR (dans le pipeline) qui le déterminera, ou écartera le lead.
    `classification_text` agrège le texte utile (activité + dénomination) pour
    cette classification.
    """

    source: str
    source_ref: str
    establishment_name: str
    city: str
    main_signal: str
    detection_date: date
    proof_text: str = ""
    proof_url: str = ""
    address: str = ""
    # Contact enrichi à la source (ex. profil Instagram) — sinon rempli plus tard.
    email: Optional[str] = None
    website: Optional[str] = None
    extra_addresses: List[str] = field(default_factory=list)  # lieux multiples (groupe)
    extra_emails: List[str] = field(default_factory=list)
    secondary_signals: List[str] = field(default_factory=list)
    # Label de cycle de vie du funnel Insta (persisté sur la fiche). None pour les
    # sources registre (BODACC/Sirene).
    lifecycle_label: Optional[str] = None
    # Population du lead : 'chr' (défaut) ou 'architecte' (prescripteur, A1).
    population: str = "chr"
    decision_maker: Optional[str] = None
    dirigeants: List[str] = field(default_factory=list)
    establishment_type: Optional[str] = None
    instagram: Optional[str] = None  # handle (source Instagram-first)
    classification_text: str = ""
    # Identifiants / enrichissement
    siren: Optional[str] = None
    naf: Optional[str] = None
    siret: Optional[str] = None
    siren_match_method: Optional[str] = None      # nom | adresse | arbitre | source
    siren_match_confidence: Optional[str] = None  # haute | moyenne
    activity_start_date: Optional[date] = None  # BODACC dateCommencementActivite
    previous_siren: Optional[str] = None  # précédent exploitant (reprise)
    venue_origin_date: Optional[date] = None  # date de création du précédent -> âge du local
    enriched: bool = False
    closed: bool = False  # établissement administrativement fermé (à écarter)
    # Géoloc (Sirene) — sert à OSM et aux liens Maps.
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    raw: Dict[str, Any] = field(default_factory=dict)


class Connector(ABC):
    """Connecteur de source de données."""

    #: identifiant court, stocké dans Opportunity.source (ex: "bodacc").
    name: str = "base"

    @abstractmethod
    def fetch(self, since_days: int, limit: int, **filters: Any) -> List[Dict[str, Any]]:
        """Récupère les enregistrements bruts de la source."""

    @abstractmethod
    def to_candidates(self, records: List[Dict[str, Any]]) -> List[LeadCandidate]:
        """Transforme les enregistrements bruts en LeadCandidate."""
