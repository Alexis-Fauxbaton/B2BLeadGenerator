"""Enrichissement Sirene via l'API publique recherche-entreprises.api.gouv.fr.

Cette API (DINUM) expose les données Sirene/INSEE SANS clé d'authentification.
On enrichit chaque lead par son SIREN : code NAF (classification fiable),
enseigne (comble les noms manquants), adresse normalisée, état administratif.

Note : l'API INSEE Sirene "brute" (api.insee.fr) nécessite une clé ; on utilise
ici le service public ouvert qui repose sur les mêmes données.
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional

import requests

from ..base import LeadCandidate

API_URL = "https://recherche-entreprises.api.gouv.fr/search"


class SireneEnricher:
    def __init__(self, timeout: int = 10, rate_delay: float = 0.15):
        self.timeout = timeout
        self.rate_delay = rate_delay
        self._cache: Dict[str, Optional[Dict[str, Any]]] = {}

    def enrich(self, candidate: LeadCandidate) -> LeadCandidate:
        """Complète le candidat à partir de son SIREN. Tolérant aux pannes :
        en cas d'échec, le candidat est renvoyé inchangé (enriched=False)."""
        if not candidate.siren:
            return candidate

        try:
            data = self._lookup(candidate.siren)
        except Exception:
            return candidate  # fail-soft : on garde la donnée BODACC

        if not data:
            return candidate

        apply_sirene_data(candidate, data)
        candidate.enriched = True
        return candidate

    def lookup(self, siren: str) -> Optional[Dict[str, Any]]:
        """Lookup public (utilisé aussi par l'enrichissement contact)."""
        return self._lookup(siren)

    def _lookup(self, siren: str) -> Optional[Dict[str, Any]]:
        if siren in self._cache:
            return self._cache[siren]

        resp = requests.get(
            API_URL, params={"q": siren, "per_page": 1}, timeout=self.timeout
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        match = next((r for r in results if str(r.get("siren")) == siren), None)
        if match is None and results:
            match = results[0]

        self._cache[siren] = match
        time.sleep(self.rate_delay)
        return match


def apply_sirene_data(candidate: LeadCandidate, data: Dict[str, Any]) -> None:
    """Applique les champs Sirene au candidat (fonction pure, testable)."""
    siege = data.get("siege") or {}

    # NAF (priorité au siège, puis à l'entreprise).
    naf = siege.get("activite_principale") or data.get("activite_principale")
    if naf:
        candidate.naf = naf

    # Nom : on n'améliore qu'avec une vraie enseigne / un nom commercial,
    # en ignorant les valeurs non diffusibles (entrepreneur ayant exercé son
    # droit d'opposition à la diffusion).
    enseignes = siege.get("liste_enseignes") or []
    enseigne = enseignes[0] if enseignes else None
    better_name = enseigne or siege.get("nom_commercial")
    if better_name and "NON-DIFFUSIBLE" not in better_name.upper():
        candidate.establishment_name = better_name.strip()

    # Adresse normalisée du siège.
    adresse = siege.get("adresse")
    if adresse:
        candidate.address = adresse.strip()

    # État administratif : "A" = actif. Sinon, établissement fermé.
    etat = data.get("etat_administratif") or siege.get("etat_administratif")
    if etat and etat.upper() != "A":
        candidate.closed = True

    # Géoloc du siège (sert à OSM + liens Maps).
    lat, lon = _to_float(siege.get("latitude")), _to_float(siege.get("longitude"))
    if lat is not None and lon is not None:
        candidate.latitude = lat
        candidate.longitude = lon

    # Trace du NAF dans la preuve.
    if naf and "NAF" not in (candidate.proof_text or ""):
        candidate.proof_text = f"{candidate.proof_text} — NAF {naf}".strip(" —")


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
