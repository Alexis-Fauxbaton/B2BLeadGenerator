"""Connecteur delta-Sirene : nouveaux SIRET CHR -> LeadCandidate — [BRIQUE 2].

Colonne vertebrale du pivot inventaire (docs/inventory-pivot-design.md) :
l'immatriculation etant obligatoire AVANT l'ouverture, le delta quotidien des
nouveaux etablissements NAF 55/56 donne un recall ~100 % sur les ouvertures
(~80/jour France, mesure 2026-07-06), y compris les creations a date FUTURE
(ouvertures pre-declarees) et les etablissements secondaires (extension de
chaines, invisibles dans BODACC).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from .base import Connector, LeadCandidate
from .enrichment.naf_classifier import classify_naf
from .insee import fetch_new_etablissements

# Codes NAF CHR — memes codes que naf_classifier (le NAF fait autorite).
CHR_NAF_CODES = ["56.10A", "56.10B", "56.10C", "56.21Z", "56.30Z", "55.10Z", "55.20Z"]
# Horizon des creations pre-declarees (date future au registre).
FUTURE_HORIZON_DAYS = 120


def _nd(value: Optional[str]) -> Optional[str]:
    """Neutralise les champs proteges '[ND]' (statut diffusion partielle)."""
    v = (value or "").strip()
    return None if not v or v == "[ND]" else v


def _title(value: Optional[str]) -> str:
    return (value or "").strip().title()


def _best_name(etab: Dict[str, Any]) -> Optional[str]:
    """Enseigne > denomination usuelle > denomination uL > prenom+nom.
    None si tout est vide/[ND] (personne physique non-diffusible)."""
    per = (etab.get("periodesEtablissement") or [{}])[0]
    ul = etab.get("uniteLegale") or {}
    for cand in (
        per.get("enseigne1Etablissement"),
        per.get("denominationUsuelleEtablissement"),
        ul.get("denominationUniteLegale"),
    ):
        if _nd(cand):
            return _nd(cand)
    prenom, nom = _nd(ul.get("prenom1UniteLegale")), _nd(ul.get("nomUniteLegale"))
    if prenom and nom:
        return f"{prenom.title()} {nom.title()}"
    return None


def _address(etab: Dict[str, Any]) -> Tuple[str, str]:
    """-> (adresse complete, ville). Champs adresse non historises."""
    adr = etab.get("adresseEtablissement") or {}
    street = " ".join(filter(None, [
        (adr.get("numeroVoieEtablissement") or "").strip(),
        (adr.get("typeVoieEtablissement") or "").strip(),
        (adr.get("libelleVoieEtablissement") or "").strip(),
    ])).strip()
    city = _title(adr.get("libelleCommuneEtablissement"))
    cp = (adr.get("codePostalEtablissement") or "").strip()
    full = ", ".join(filter(None, [street, " ".join(filter(None, [cp, city]))]))
    return full, city


def map_etablissement(etab: Dict[str, Any], today: date) -> Optional[LeadCandidate]:
    """Etablissement INSEE brut -> LeadCandidate, ou None si inexploitable
    (ferme, hors CHR, ou anonyme [ND] sans enseigne). Fonction PURE."""
    per = (etab.get("periodesEtablissement") or [{}])[0]
    if (per.get("etatAdministratifEtablissement") or "A") != "A":
        return None
    naf = per.get("activitePrincipaleEtablissement")
    if not (naf and classify_naf(naf)):
        return None
    name = _best_name(etab)
    if not name:
        return None  # injoignable ET inmatchable : sans valeur commerciale
    created = _ymd(etab.get("dateCreationEtablissement"))
    address, city = _address(etab)

    secondary: List[str] = []
    ul = etab.get("uniteLegale") or {}
    if etab.get("etablissementSiege") is False:
        # Nouvel etablissement d'une societe existante = expansion multi-sites
        # (invisible dans BODACC — un des apports du delta).
        secondary.append("extension multi-sites")

    if created and created > today:
        proof = (f"Création d'établissement pré-déclarée au registre pour le "
                 f"{created.isoformat()} (NAF {naf}).")
    else:
        proof = (f"Établissement créé le {created.isoformat() if created else '?'} "
                 f"au registre Sirene (NAF {naf}).")

    return LeadCandidate(
        source="sirene",
        source_ref=etab.get("siret") or "",
        establishment_name=name,
        city=city or "",
        address=address,
        main_signal="ouverture prochaine",
        secondary_signals=secondary,
        detection_date=today,
        activity_start_date=created,
        classification_text=name,
        siren=etab.get("siren"),
        naf=naf,
        proof_text=proof,
        raw=etab,
    )


def _ymd(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


class SireneDeltaConnector(Connector):
    """Delta des nouveaux SIRET CHR (INSEE). `departments` = prefixes de CP.
    La fenetre remonte de `since_days` ET s'etend a +FUTURE_HORIZON_DAYS
    (creations pre-declarees). `since_date` (curseur incremental) prime sur
    since_days quand fourni."""
    name = "sirene"

    def __init__(self) -> None:
        self.last_total_count = 0

    def fetch(self, since_days: int = 7, limit: int = 3000,
              departments: Optional[List[str]] = None,
              since_date: Optional[date] = None, **_: Any) -> List[Dict[str, Any]]:
        today = date.today()
        date_from = since_date or (today - timedelta(days=since_days or 7))
        date_to = today + timedelta(days=FUTURE_HORIZON_DAYS)
        records = fetch_new_etablissements(
            date_from, date_to, CHR_NAF_CODES,
            cp_prefixes=departments, limit=limit,
        )
        self.last_total_count = len(records)
        return records

    def to_candidates(self, records: List[Dict[str, Any]]) -> List[LeadCandidate]:
        today = date.today()
        out: List[LeadCandidate] = []
        for etab in records:
            cand = map_etablissement(etab, today)
            if cand:
                out.append(cand)
        return out
