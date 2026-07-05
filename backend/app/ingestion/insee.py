"""Client API Sirene INSEE (delta des nouveaux SIRET) — [BRIQUE 2].

Syntaxe validee live (2026-07-06) : les champs historises comme
`activitePrincipaleEtablissement` DOIVENT etre requetes via `periode(...)`
(sinon 400 « Erreur de syntaxe ») ; `dateCreationEtablissement` accepte les
plages ET les dates futures (creations pre-declarees = ouvertures annoncees
au registre). Pagination par curseur ; fin quand curseurSuivant == curseur.
Cle gratuite requise (portail-api.insee.fr) : env `INSEE_API_KEY`.
Fail-soft : pas de cle / erreur -> [] ou liste partielle, jamais d'exception.
"""
from __future__ import annotations

import os
import time
from datetime import date
from typing import Any, Callable, Dict, List, Optional, Sequence

import requests

SIRET_URL = "https://api.insee.fr/api-sirene/3.11/siret"
_MIN_INTERVAL = 2.1  # 30 req/min
_PAGE_SIZE = 1000    # max autorise par l'API
_last_call = [0.0]

InseeFetch = Callable[[str, Dict[str, Any], Dict[str, str]], Dict[str, Any]]


def has_insee_key() -> bool:
    return bool(os.getenv("INSEE_API_KEY"))


def build_query(date_from: date, date_to: date, naf_codes: Sequence[str],
                cp_prefixes: Optional[Sequence[str]] = None) -> str:
    """Construit le parametre q (pure, testable)."""
    date_part = f"dateCreationEtablissement:[{date_from.isoformat()} TO {date_to.isoformat()}]"
    naf_part = " OR ".join(f"activitePrincipaleEtablissement:{c}" for c in naf_codes)
    parts = [date_part, f"periode({naf_part})"]
    if cp_prefixes:
        cp_part = " OR ".join(f"codePostalEtablissement:{p}*" for p in cp_prefixes)
        parts.append(f"({cp_part})")
    return " AND ".join(parts)


def _http_get(url: str, params: Dict[str, Any], headers: Dict[str, str]) -> Dict[str, Any]:
    """GET throttle (30 req/min), fail-soft {}."""
    wait = _MIN_INTERVAL - (time.monotonic() - _last_call[0])
    if wait > 0:
        time.sleep(wait)
    _last_call[0] = time.monotonic()
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return {}


def fetch_new_etablissements(
    date_from: date, date_to: date, naf_codes: Sequence[str],
    cp_prefixes: Optional[Sequence[str]] = None,
    limit: int = 3000,
    fetch: Optional[InseeFetch] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Tous les etablissements crees dans la fenetre (pagination curseur).
    `meta`, si fourni, recoit `total` (header.total de la 1re page reussie) —
    sert a detecter une fenetre tronquee (plus d'annonces que `limit`)."""
    key = os.getenv("INSEE_API_KEY")
    if not key:
        return []
    fetch = fetch or _http_get
    headers = {"X-INSEE-Api-Key-Integration": key}
    q = build_query(date_from, date_to, naf_codes, cp_prefixes)
    out: List[Dict[str, Any]] = []
    curseur = "*"
    first_page = True
    while len(out) < limit:
        nombre = min(_PAGE_SIZE, limit - len(out))
        data = fetch(SIRET_URL, {"q": q, "nombre": nombre, "curseur": curseur}, headers)
        header = data.get("header") or {}
        if header.get("statut") != 200:
            break  # fail-soft : on garde ce qu'on a
        if first_page and meta is not None:
            meta["total"] = header.get("total")
            first_page = False
        etablissements = data.get("etablissements") or []
        if not etablissements:
            break  # page vide : plus rien a recuperer (garde-fou boucle infinie)
        out.extend(etablissements)
        suivant = header.get("curseurSuivant")
        if not suivant or suivant == curseur:
            break
        curseur = suivant
    return out[:limit]
