"""Connecteur délta JEUNES STUDIOS d'architecture d'intérieur (A2).

Réutilise la brique 2 (insee.fetch_new_etablissements, throttle 2,1 s intégré)
pointée sur NAF 71.11Z/74.10Z, fenêtre de création RÉCENTE (pas le stock).
Sonde-a2 volet 2 : flux RECALL-ORIENTÉ mais BRUYANT (91 % d'EI) et AVEUGLE
(65 % de dénominations masquées [ND]). Filtre de qualification mots-clés mesuré
(28 % des dénominations visibles ; ~5 studios qualifiables/jour) + garde négatif
anti-bruit 74.10Z (design graphique). Flux faible priorité -> lifecycle 'unknown',
PAS de tier. SIREN/dirigeant/ancienneté NATIFS (aucun matcher requis)."""
from __future__ import annotations

import unicodedata
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from .base import Connector, LeadCandidate
from .insee import fetch_new_etablissements
from .sirene_delta import IDF_CP_PREFIXES, _address, _best_name, _nd, _ymd

ARCHI_NAF_CODES = ["71.11Z", "74.10Z"]

# Mots-clés de qualification sur la dénomination (sonde #9, rendement 28 % visible).
QUALIF_KEYWORDS = ("interieur", "design", "studio", "agencement", "deco",
                   "archi", "atelier", "concept", "home", "espace")
# Garde négatif : le NAF 74.10Z couvre le design graphique/produit (bruit adjacent).
NEG_KEYWORDS = ("graphique", "graphic", "graphisme", "web", "ux", "ui",
                "packaging", "motion")


def _norm(text: Optional[str]) -> str:
    t = unicodedata.normalize("NFD", (text or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def qualifies(name: Optional[str]) -> bool:
    """True si la dénomination porte un mot-clé métier ET aucun mot-clé négatif.
    Vide / [ND] -> False (injoignable ET inqualifiable). PURE."""
    n = _norm(name)
    if not n or n == "[nd]":
        return False
    if any(neg in n for neg in NEG_KEYWORDS):
        return False
    return any(kw in n for kw in QUALIF_KEYWORDS)


def map_jeune_studio(etab: Dict[str, Any], today: date) -> Optional[LeadCandidate]:
    """Établissement INSEE archi -> LeadCandidate 'architecte', ou None (fermé,
    hors NAF archi, dénomination masquée/absente, ou non qualifiée). PURE."""
    per = (etab.get("periodesEtablissement") or [{}])[0]
    if (per.get("etatAdministratifEtablissement") or "A") != "A":
        return None
    naf = per.get("activitePrincipaleEtablissement")
    if naf not in ARCHI_NAF_CODES:
        return None
    name = _best_name(etab)  # enseigne > denom usuelle > denom UL > prénom+nom
    if not name or not qualifies(name):
        return None
    created = _ymd(etab.get("dateCreationEtablissement"))
    address, city = _address(etab)

    ul = etab.get("uniteLegale") or {}
    prenom, nom = _nd(ul.get("prenom1UniteLegale")), _nd(ul.get("nomUniteLegale"))
    decision_maker = f"{prenom.title()} {nom.title()}" if (prenom and nom) else None

    proof = (f"Studio récemment créé le {created.isoformat() if created else '?'} "
             f"au registre Sirene (NAF {naf}, activité de conception d'espaces).")

    return LeadCandidate(
        source="jeunes_studios",
        source_ref=etab.get("siret") or "",
        establishment_name=name,
        city=city or "",
        address=address,
        main_signal="prescripteur actif",
        secondary_signals=["jeune studio (création récente)"],
        lifecycle_label="unknown",
        population="architecte",
        establishment_type="architecte d'intérieur",
        decision_maker=decision_maker,
        detection_date=today,
        activity_start_date=created,
        classification_text=name,
        siren=etab.get("siren"),
        naf=naf,
        siret=etab.get("siret"),
        siren_match_method="source",
        proof_text=proof,
        raw=etab,
    )


class JeunesStudiosConnector(Connector):
    """Délta des nouveaux SIRET archi (INSEE). `departments` : None/['france'] ->
    France entière ; liste -> préfixes de CP. Fenêtre = `since_days` derniers jours
    jusqu'à aujourd'hui (PAS d'horizon futur : un jeune studio est déjà créé)."""
    name = "jeunes_studios"

    def __init__(self) -> None:
        self.last_total_count = 0

    def fetch(self, since_days: int = 30, limit: int = 1000,
              departments: Optional[List[str]] = None,
              since_date: Optional[date] = None, **_: Any) -> List[Dict[str, Any]]:
        today = date.today()
        date_from = since_date or (today - timedelta(days=since_days or 30))
        if departments is None or departments == ["france"]:
            cp_prefixes: Optional[List[str]] = None
        elif departments == ["idf"]:
            cp_prefixes = IDF_CP_PREFIXES
        else:
            cp_prefixes = departments
        meta: Dict[str, Any] = {}
        records = fetch_new_etablissements(
            date_from, today, ARCHI_NAF_CODES,
            cp_prefixes=cp_prefixes, limit=limit, meta=meta,
        )
        self.last_total_count = meta.get("total") or len(records)
        return records

    def to_candidates(self, records: List[Dict[str, Any]]) -> List[LeadCandidate]:
        today = date.today()
        out: List[LeadCandidate] = []
        for etab in records:
            cand = map_jeune_studio(etab, today)
            if cand:
                out.append(cand)
        return out
