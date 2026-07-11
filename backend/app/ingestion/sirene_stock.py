"""Connecteur STOCK Sirene 74.10Z + 71.11Z d'architecture d'interieur (B1).

Contrairement au delta A2 (`jeunes_studios.py`, fenetre de creations recentes),
ce connecteur interroge l'INTEGRALITE du stock d'etablissements ACTIFS (etat=A)
sans fenetre de date -- decoupe par departement, curseur INSEE jusqu'a
epuisement (`fetch_stock_etablissements`, T1). Rendement mesure par la sonde
(.superpowers/sdd/sonde-volume/) : filtre mots-clais 74.10Z ~9,3 % du stock ->
~28 000 qualifies sur les 308 629 unites actives. Reutilise TEL QUEL le filtre
`jeunes_studios.qualifies` (memes mots-cles positifs + gardes negatives
design graphique/produit/corporate). Le NAF 71.11Z (architecture batiment)
est quasi inexploitable par mots-cles seuls (0/300 denominations avec
"interieur" dans la sonde) -> qualification renforcee par co-occurrence
STRICTE archi*/decorat* + interieur (`qualifies_71`), volume quasi nul MAIS
0 faux-ami batiment (VIDE > FAUX). `[ND]` (denomination masquee) ecarte
(injoignable ET inqualifiable, taux mesure 0 % sur le stock). Booster
"jeune studio (creation recente)" si `dateCreationEtablissement` < 18 mois
(moment favorable, complementaire au flux delta A2). SIREN/dirigeant/
anciennete NATIFS (record INSEE) -> `siren_match_method='source'`, aucun
matcher requis. `lifecycle_label='unknown'` (pas de tier funnel Insta).
"""
from __future__ import annotations

import unicodedata
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from .base import Connector, LeadCandidate
from .insee import fetch_stock_etablissements
from .jeunes_studios import qualifies
from .sirene_delta import IDF_CP_PREFIXES, _address, _best_name, _nd, _ymd

STOCK_NAF_CODES = ["74.10Z", "71.11Z"]
RECENT_MONTHS = 18  # booster "moment" -- creation recente = studio jeune

# Co-occurrence STRICTE pour 71.11Z (architecture batiment par defaut) : la
# denomination doit porter a la fois un radical archi/decorat ET "interieur"
# (sonde #3 : 0/300 denominations 71.11Z contiennent "interieur" -> le filtre
# mots-cles seul de `qualifies` capterait quasi exclusivement du batiment).
_ARCHI_STEMS = ("archi", "decorat")


def _norm(text: Optional[str]) -> str:
    t = unicodedata.normalize("NFD", (text or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def qualifies_71(name: Optional[str]) -> bool:
    """True si la denomination porte un radical archi*/decorat* ET "interieur"
    (co-occurrence stricte -- decision #3, VIDE > FAUX sur 71.11Z). PURE."""
    n = _norm(name)
    if not n or n == "[nd]":
        return False
    has_stem = any(stem in n for stem in _ARCHI_STEMS)
    return has_stem and "interieur" in n


def map_stock_etablissement(etab: Dict[str, Any], today: date) -> Optional[LeadCandidate]:
    """Etablissement INSEE (stock) -> LeadCandidate 'architecte', ou None
    (ferme, hors NAF stock, denomination masquee/absente, ou non qualifie
    selon le filtre propre au NAF). Fonction PURE."""
    per = (etab.get("periodesEtablissement") or [{}])[0]
    if (per.get("etatAdministratifEtablissement") or "A") != "A":
        return None
    naf = per.get("activitePrincipaleEtablissement")
    if naf not in STOCK_NAF_CODES:
        return None
    name = _best_name(etab)  # None si [ND] sans enseigne -> injoignable
    if not name:
        return None
    if naf == "74.10Z":
        if not qualifies(name):
            return None
    else:  # 71.11Z
        if not qualifies_71(name):
            return None

    created = _ymd(etab.get("dateCreationEtablissement"))
    address, city = _address(etab)

    ul = etab.get("uniteLegale") or {}
    prenom, nom = _nd(ul.get("prenom1UniteLegale")), _nd(ul.get("nomUniteLegale"))
    decision_maker = f"{prenom.title()} {nom.title()}" if (prenom and nom) else None

    secondary: List[str] = ["stock sirene"]
    if created and created >= today - timedelta(days=RECENT_MONTHS * 30):
        secondary.append("jeune studio (création récente)")

    proof = (f"Etablissement actif au registre Sirene (NAF {naf}, stock, "
             f"cree le {created.isoformat() if created else '?'}).")

    return LeadCandidate(
        source="sirene_stock",
        source_ref=etab.get("siret") or "",
        establishment_name=name,
        city=city or "",
        address=address,
        main_signal="prescripteur actif",
        secondary_signals=secondary,
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


class SireneStockConnector(Connector):
    """Stock complet des SIRET archi actifs (INSEE, sans fenetre de date).
    `departments` : None/['france'] -> France entiere ; ['idf'] -> IDF ;
    liste -> prefixes de CP explicites (ex. ['69'], ['75']). `limit=0`
    (defaut) = curseur jusqu'a epuisement (borne les enregistrements BRUTS,
    pas les qualifies -- la qualification a lieu en aval dans
    `to_candidates`). `cursor` permet de reprendre un departement geant
    (curseur expose sur `self.last_cursor` apres l'appel)."""
    name = "sirene_stock"

    def __init__(self) -> None:
        self.last_total_count = 0
        self.last_cursor = ""

    def fetch(self, departments: Optional[List[str]] = None, limit: int = 0,
              cursor: str = "*", since_days: int = 0,
              since_date: Optional[date] = None, max_pages: int = 0,
              **_: Any) -> List[Dict[str, Any]]:
        if departments is None or departments == ["france"]:
            cp_prefixes: Optional[List[str]] = None
        elif departments == ["idf"]:
            cp_prefixes = IDF_CP_PREFIXES
        else:
            cp_prefixes = departments
        meta: Dict[str, Any] = {}
        records, next_cursor = fetch_stock_etablissements(
            STOCK_NAF_CODES, cp_prefixes=cp_prefixes, limit=limit,
            cursor=cursor, meta=meta,
        )
        self.last_total_count = meta.get("total") or len(records)
        self.last_cursor = next_cursor
        return records

    def to_candidates(self, records: List[Dict[str, Any]]) -> List[LeadCandidate]:
        today = date.today()
        out: List[LeadCandidate] = []
        for etab in records:
            cand = map_stock_etablissement(etab, today)
            if cand:
                out.append(cand)
        return out
