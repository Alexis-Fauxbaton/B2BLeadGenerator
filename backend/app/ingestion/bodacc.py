"""Connecteur BODACC — annonces commerciales (opendatasoft, sans authentification).

Extract : interroge l'API, filtre (Île-de-France / départements, familles d'avis,
mots-clés CHR, fenêtre de date) et pagine.
Mapping : transforme chaque annonce en LeadCandidate normalisé.
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

import requests

from .base import Connector, LeadCandidate

API_URL = (
    "https://bodacc-datadila.opendatasoft.com/api/explore/v2.1/"
    "catalog/datasets/annonces-commerciales/records"
)

# region_code 11 = Île-de-France.
IDF_REGION_CODE = 11

# Mots-clés CHR pour le filtre plein-texte côté API (large ; affiné ensuite
# par le classifier).
CHR_KEYWORDS = ["restaurant", "restauration", "café", "brasserie", "hôtel", "bar", "traiteur"]

# Familles d'avis BODACC -> signal métier de l'app.
FAMILY_TO_SIGNAL = {
    "creation": "création récente",
    "vente": "reprise",
    "modification": "changement propriétaire",
}

PAGE_SIZE = 100
MAX_PAGES = 20  # garde-fou (≤ 2000 annonces par run)


class BodaccConnector(Connector):
    name = "bodacc"

    def __init__(self, timeout: int = 30, page_delay: float = 0.2):
        self.timeout = timeout
        self.page_delay = page_delay
        # Nombre total d'annonces correspondant au dernier filtre (pour détecter
        # une fenêtre tronquée).
        self.last_total_count: Optional[int] = None

    # --- Extract --------------------------------------------------------------

    def _build_where(
        self,
        since_days: int,
        departments: Optional[List[str]],
        families: List[str],
        since_date: Optional[date] = None,
    ) -> str:
        if since_date is None:
            since_date = date.today() - timedelta(days=since_days)
        since_iso = since_date.isoformat()

        clauses: List[str] = []

        if departments:
            dept_list = ", ".join(f'"{d.strip()}"' for d in departments if d.strip())
            clauses.append(f"numerodepartement in ({dept_list})")
        else:
            clauses.append(f"region_code={IDF_REGION_CODE}")

        fam_list = ", ".join(f'"{f}"' for f in families)
        clauses.append(f"familleavis in ({fam_list})")

        clauses.append(f'dateparution >= "{since_iso}"')

        chr_clause = " or ".join(f'search(*,"{kw}")' for kw in CHR_KEYWORDS)
        clauses.append(f"({chr_clause})")

        return " and ".join(clauses)

    def fetch(
        self,
        since_days: int = 90,
        limit: int = 100,
        departments: Optional[List[str]] = None,
        families: Optional[List[str]] = None,
        since_date: Optional[date] = None,
        max_pages: int = MAX_PAGES,
        **_: Any,
    ) -> List[Dict[str, Any]]:
        families = families or list(FAMILY_TO_SIGNAL.keys())
        where = self._build_where(since_days, departments, families, since_date)
        self.last_total_count = None

        records: List[Dict[str, Any]] = []
        offset = 0
        pages = 0
        while len(records) < limit and pages < max_pages:
            page_size = min(PAGE_SIZE, limit - len(records))
            params = {
                "where": where,
                "limit": page_size,
                "offset": offset,
                "order_by": "dateparution desc",
            }
            data = self._get(params)
            if self.last_total_count is None:
                self.last_total_count = data.get("total_count", 0)
            results = data.get("results", [])
            if not results:
                break
            records.extend(results)
            offset += page_size
            pages += 1
            if offset >= data.get("total_count", 0):
                break
            time.sleep(self.page_delay)

        return records

    def _get(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """GET avec un retry simple en cas d'erreur réseau."""
        last_exc: Optional[Exception] = None
        for attempt in range(2):
            try:
                resp = requests.get(API_URL, params=params, timeout=self.timeout)
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException as exc:  # réseau, timeout, 5xx...
                last_exc = exc
                time.sleep(0.5 * (attempt + 1))
        raise RuntimeError(f"Échec de l'appel BODACC : {last_exc}")

    # --- Mapping --------------------------------------------------------------

    def to_candidates(self, records: List[Dict[str, Any]]) -> List[LeadCandidate]:
        candidates: List[LeadCandidate] = []
        for rec in records:
            try:
                candidate = self._map_record(rec)
            except Exception:
                # Un enregistrement malformé ne casse pas le batch ; il sera
                # compté en "errors" par le pipeline (qui réessaie le mapping).
                continue
            if candidate:
                candidates.append(candidate)
        return candidates

    def _map_record(self, rec: Dict[str, Any]) -> Optional[LeadCandidate]:
        family = (rec.get("familleavis") or "").lower()
        persons = _parse_json(rec.get("listepersonnes"))
        establishments = _parse_json(rec.get("listeetablissements"))
        prev_owner = rec.get("listeprecedentproprietaire")
        prev_operator = rec.get("listeprecedentexploitant")
        origine_fonds = _establishment_origine(establishments)
        acte = _parse_json(rec.get("acte"))
        activity_start = (
            _parse_date_opt(acte.get("dateCommencementActivite"))
            if isinstance(acte, dict) else None
        )

        # Signal principal
        main_signal = FAMILY_TO_SIGNAL.get(family)
        if family == "modification" and not (prev_owner or prev_operator):
            # Une modification sans changement de propriétaire/exploitant n'est
            # pas un moment d'achat exploitable.
            return None
        if not main_signal:
            return None

        # création/reprise via le REGISTRE (autoritatif) : le champ "Origine du
        # fond" tranche ("Création d'un fonds de commerce" vs "Achat au précédent
        # exploitant…"), complété par la présence d'un précédent exploitant/
        # propriétaire. Évite de prendre un vieux lieu repris pour une ouverture
        # neuve — et n'invente pas une reprise là où le registre dit "création".
        if family == "creation" and _is_takeover(origine_fonds, prev_owner, prev_operator):
            main_signal = "reprise"

        # Signaux secondaires
        secondary: List[str] = []
        if (prev_owner or prev_operator) and main_signal != "changement propriétaire":
            secondary.append("changement propriétaire")

        # Nom, activité, adresse, décideur
        person = _first_person(persons)
        # Priorité au nom d'enseigne / dénomination ; le nom civil (commercant)
        # n'est qu'un dernier recours (cas des entreprises individuelles sans
        # enseigne déclarée).
        name = (
            _get(person, "nomCommercial")
            or _get(person, "denomination")
            or _establishment_name(establishments)
            or rec.get("commercant")
            or _person_full_name(person)
            or "Établissement (BODACC)"
        )
        activite = _extract_activite(person, establishments)
        address = _extract_address(person, rec)
        # Personne physique (entreprise individuelle) -> son nom. Société (pm) ->
        # on lit TOUS les dirigeants déclarés dans `administration` (Président, DG,
        # Gérant…). `decision_maker` = le principal ; `dirigeants` = la liste.
        if _is_physical(person):
            decision_maker = _person_full_name(person) or None
            dirigeants = [decision_maker] if decision_maker else []
        else:
            dirigeants = _parse_dirigeants(person.get("administration"))
            decision_maker = dirigeants[0] if dirigeants else None

        classification_text = " ".join(filter(None, [activite, name]))

        detection = _parse_date(rec.get("dateparution"))

        proof_text = _build_proof(rec, family, activite)
        siren = _extract_siren(rec, person)

        return LeadCandidate(
            source=self.name,
            source_ref=str(rec.get("id")),
            establishment_name=name.strip(),
            city=(rec.get("ville") or "").strip(),
            address=address,
            main_signal=main_signal,
            secondary_signals=secondary,
            detection_date=detection,
            decision_maker=decision_maker,
            dirigeants=dirigeants,
            classification_text=classification_text,
            siren=siren,
            activity_start_date=activity_start,
            proof_text=proof_text,
            proof_url=rec.get("url_complete") or "",
            raw=rec,
        )


# --- Helpers de parsing -------------------------------------------------------


def _parse_json(value: Any) -> Any:
    if not value:
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return None


def _first_person(persons: Any) -> Dict[str, Any]:
    if isinstance(persons, dict):
        node = persons.get("personne", persons)
    elif isinstance(persons, list) and persons:
        node = persons[0].get("personne", persons[0]) if isinstance(persons[0], dict) else {}
    else:
        return {}
    if isinstance(node, list):
        node = node[0] if node else {}
    return node if isinstance(node, dict) else {}


def _get(node: Any, key: str) -> str:
    if isinstance(node, dict):
        val = node.get(key)
        if isinstance(val, str):
            return val
    return ""


def _is_physical(person: Dict[str, Any]) -> bool:
    return person.get("typePersonne") == "pp" or bool(person.get("nom"))


def _person_full_name(person: Dict[str, Any]) -> str:
    nom = _get(person, "nom")
    prenom = _get(person, "prenom")
    full = " ".join(filter(None, [prenom, nom])).strip()
    return full


def _establishment_name(establishments: Any) -> str:
    """Cherche une enseigne / un nom d'établissement (si présent)."""
    node = establishments
    if isinstance(node, dict):
        node = node.get("etablissement", node)
    items = node if isinstance(node, list) else [node]
    for e in items:
        if isinstance(e, dict):
            for key in ("enseigne", "nom", "nomCommercial"):
                val = _get(e, key)
                if val:
                    return val
    return ""


def _establishment_origine(establishments: Any) -> str:
    """Récupère 'origineFonds' de l'établissement (ex: 'Création d'un fonds de
    commerce' vs 'Achat au précédent exploitant…')."""
    node = establishments
    if isinstance(node, dict):
        node = node.get("etablissement", node)
    items = node if isinstance(node, list) else [node]
    for e in items:
        if isinstance(e, dict):
            val = _get(e, "origineFonds")
            if val:
                return val
    return ""


def _bodacc_norm(text: Any) -> str:
    text = (str(text) if text is not None else "").lower()
    return "".join(
        c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn"
    )


_ROLE_RE = re.compile(
    r"(Président[e]?(?: directeur général)?|G[ée]rant[e]?|Co-?g[ée]rant[e]?|"
    r"Directeur général|Directrice générale|Directeur|Directrice|Associé[e]? unique)"
    r"\s*:\s*",
    re.IGNORECASE,
)
# Priorité du décideur principal à retenir.
_ROLE_PRIORITY = ["president", "gerant", "directeur general", "directeur", "associe"]


def _dirigeant_rank(role: str) -> int:
    r = _bodacc_norm(role)
    for i, key in enumerate(_ROLE_PRIORITY):
        if key in r:
            return i
    return len(_ROLE_PRIORITY)


def _parse_dirigeants(administration: Any) -> List[str]:
    """Extrait TOUS les dirigeants du champ `administration` d'une société,
    triés par importance (Président d'abord). Ex: 'Président : Afif, Samuel Serge
    Elie, Directeur général : Journo, Victor Isaac' -> ['Samuel Afif, Président',
    'Victor Journo, Directeur général']."""
    if not isinstance(administration, str) or not administration.strip():
        return []
    matches = list(_ROLE_RE.finditer(administration))
    if not matches:
        return []
    pairs: List[tuple] = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(administration)
        name = administration[start:end].strip().strip(",").strip()
        if name:
            pairs.append((m.group(1), name))
    pairs.sort(key=lambda p: _dirigeant_rank(p[0]))
    return [_format_dirigeant(name, role) for role, name in pairs]


def _parse_dirigeant(administration: Any) -> Optional[str]:
    """Dirigeant principal (Président en priorité) ou None."""
    dirigeants = _parse_dirigeants(administration)
    return dirigeants[0] if dirigeants else None


def _format_dirigeant(name: str, role: str) -> str:
    """'Afif, Samuel Serge Elie' + 'Président' -> 'Samuel Afif, Président'.
    On garde la virgule : le scoring valorise un décideur nommé (présence d'une
    virgule = rôle identifié)."""
    display = name
    if "," in name:
        nom_part, prenoms = name.split(",", 1)
        first = prenoms.split()
        display = f"{first[0]} {nom_part.strip()}".strip() if first else nom_part.strip()
    return f"{display}, {role.strip().capitalize()}"


def _is_takeover(origine_fonds: str, prev_owner: Any, prev_operator: Any) -> bool:
    """Une création est en fait une REPRISE si l'origine du fonds est un achat,
    ou si un précédent exploitant/propriétaire est nommé."""
    o = _bodacc_norm(origine_fonds)
    if any(k in o for k in ("achat", "precedent", "reprise")):
        return True
    return bool(prev_owner or prev_operator)


def _extract_activite(person: Dict[str, Any], establishments: Any) -> str:
    parts: List[str] = []
    act = _get(person, "activite")
    if act:
        parts.append(act)
    # Établissements : peut contenir une activité plus précise.
    node = establishments
    if isinstance(node, dict):
        node = node.get("etablissement", node)
    if isinstance(node, list):
        for e in node:
            if isinstance(e, dict):
                parts.append(_get(e, "activite"))
    elif isinstance(node, dict):
        parts.append(_get(node, "activite"))
    return " ".join(p for p in parts if p).strip()


def _extract_address(person: Dict[str, Any], rec: Dict[str, Any]) -> str:
    addr = person.get("adresseSiegeSocial") if isinstance(person, dict) else None
    if isinstance(addr, dict):
        bits = [
            _get(addr, "numeroVoie"),
            _get(addr, "typeVoie"),
            _get(addr, "nomVoie"),
            _get(addr, "codePostal"),
            _get(addr, "ville"),
        ]
        line = " ".join(b for b in bits if b).strip()
        if line:
            return line
    # Repli : ville + code postal de l'annonce.
    return " ".join(filter(None, [rec.get("cp"), rec.get("ville")])).strip()


def _build_proof(rec: Dict[str, Any], family: str, activite: str) -> str:
    lib = rec.get("familleavis_lib") or family
    bits = [f"Annonce BODACC ({lib})"]
    if activite:
        bits.append(f"activité : {activite}")
    jugement = _parse_json(rec.get("jugement"))
    if isinstance(jugement, dict) and jugement.get("nature"):
        bits.append(str(jugement["nature"]))
    acte = _parse_json(rec.get("acte"))
    if isinstance(acte, dict):
        cat = acte.get("categorieVente") or acte.get("typeVente")
        if cat:
            bits.append(str(cat))
    return " — ".join(bits)


def _extract_siren(rec: Dict[str, Any], person: Dict[str, Any]) -> Optional[str]:
    """Récupère le SIREN (9 chiffres) depuis le registre ou le numéro
    d'immatriculation."""
    candidates: List[str] = []
    registre = rec.get("registre")
    if isinstance(registre, list):
        candidates.extend(str(r) for r in registre)
    elif registre:
        candidates.append(str(registre))
    immat = person.get("numeroImmatriculation") if isinstance(person, dict) else None
    if isinstance(immat, dict):
        candidates.append(_get(immat, "numeroIdentification"))

    for cand in candidates:
        digits = "".join(ch for ch in str(cand) if ch.isdigit())
        if len(digits) >= 9:
            return digits[:9]
    return None


def _parse_date(value: Any) -> date:
    if not value:
        return date.today()
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return date.today()


def _parse_date_opt(value: Any) -> Optional[date]:
    """Comme _parse_date mais renvoie None si absent/invalide (pas date.today())."""
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None
