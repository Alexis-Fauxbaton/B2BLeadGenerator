"""Balayage Google Places (New) par ville — architectes d'intérieur (B2, T3).

Contrairement à `enrichment.places.lookup_places` (enrichissement CHR, 1
fiche/appel, gate `_is_chr_type`), ce connecteur balaie les villes ordonnées
par population avec `search_places_text` (jusqu'à 20 fiches enrichies par
appel FACTURÉ, décision #7) et AUCUN gate CHR -- une garde POSITIVE légère
(`_archi_ok`, réutilise `jeunes_studios.qualifies`) filtre les faux-amis.
Plafond structurel ~60 résultats/ville (3 pages x 20, sonde #6) -> 2 requêtes
par ville (« architecte d'intérieur » / « décorateur d'intérieur »),
dédupliquées par `place_id`. Téléphone/site NATIFS quasi-totaux (sonde :
téléphone 96,7 %, site 100 %).

Budget € DUR (`EUR_PER_CALL`, SKU Text Search Enterprise, décision #8) :
le balayage s'arrête PROPREMENT (fail-soft) dès que le prochain appel
dépasserait `budget_eur` -- jamais d'exception, jamais de dépassement.
`spend_eur` n'est incrémenté QUE si l'appel a réellement été facturé
(`search_places_text` renvoie `billed=True` -- réponse Google reçue) : une
clé `GOOGLE_PLACES_API_KEY` absente/vide ou une exception réseau avalée ne
coûte jamais de budget factice, et arrête tout le balayage proprement sans
avancer le checkpoint sur la ville en cours (reprise correcte dès que la
clé est disponible).
`CityCheckpoint` persiste `{month, next_city_index, spend_eur}` pour étaler
le balayage top-N villes sur plusieurs jours/mois (reset automatique au
changement de mois, cf. décision #8 -- le pool gratuit Enterprise est
MENSUEL et PARTAGÉ avec `lookup_places`)."""
from __future__ import annotations

import json
import os
import re
import unicodedata
from datetime import date
from typing import Any, Callable, Dict, List, Optional, Set

from .base import Connector, LeadCandidate
from .data.villes_fr import VILLES_FR
from .enrichment.places import search_places_text
from .jeunes_studios import qualifies

# SKU Text Search Enterprise : 40 $/1000 ~= 0,037 EUR/appel au palier
# 0-100k (décision #7/#8). À METTRE À JOUR si le SKU/tarif change.
EUR_PER_CALL = 0.037

# 2 requêtes par ville (sonde #6 -- élargit le recall, dédup par place_id).
QUERIES = ("architecte d'intérieur {ville}", "décorateur d'intérieur {ville}")

_DEFAULT_CHECKPOINT_PATH = "data/places_checkpoint.json"

# "hotel"/"restaurant" : mot-clé assez long pour ne pas produire de faux
# positif en sous-chaine -- gardé en containment simple (couvre aussi les
# pluriels "hotels"/"restaurants"). "chr" est un token COURT et DOIT être
# délimité par des frontières de mot (\bchr\b, cf. `_CHR_TOKEN_RE`) : en
# sous-chaine naive il matche des patronymes français courants ("Chretien",
# "Chraibi", "Dechriste"...), taggant à tort un architecte sans lien
# hospitality en tier T2.
_HOSPITALITY_KEYWORDS = ("hotel", "restaurant")
_CHR_TOKEN_RE = re.compile(r"\bchr\b")


def _norm(text: Optional[str]) -> str:
    t = unicodedata.normalize("NFD", (text or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def _archi_ok(name: Optional[str]) -> bool:
    """Garde positive légère : réutilise `jeunes_studios.qualifies` (mot-clé
    métier présent, faux-ami trimé). PURE."""
    return qualifies(name)


def _hospitality(name: Optional[str]) -> bool:
    """True si le nom Places porte un mot-clé hôtel/restaurant (sous-chaine,
    accents normalisés par `_norm`), ou le token métier CHR en tant que MOT
    ENTIER (`\\bchr\\b` -- pas une sous-chaine : évite les faux positifs sur
    des patronymes comme "Chretien"/"Chraibi"/"Dechriste"). Tier T2,
    `portfolio hospitality/CHR`. PURE."""
    n = _norm(name)
    if any(kw in n for kw in _HOSPITALITY_KEYWORDS):
        return True
    return bool(_CHR_TOKEN_RE.search(n))


class CityCheckpoint:
    """Checkpoint de reprise MENSUELLE du balayage (`{month, next_city_index,
    spend_eur}`, JSON). Reset automatique si le mois courant diffère du mois
    persisté (nouveau budget mensuel -- décision #8, pool gratuit Enterprise
    partagé avec `lookup_places`, RENOUVELÉ chaque mois)."""

    def __init__(self, path: str = _DEFAULT_CHECKPOINT_PATH) -> None:
        self.path = path
        self.month = date.today().strftime("%Y-%m")
        self.next_city_index = 0
        self.spend_eur = 0.0
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return
        if data.get("month") != self.month:
            return  # nouveau mois -> reste sur next_city_index=0, spend_eur=0.0
        self.next_city_index = int(data.get("next_city_index") or 0)
        self.spend_eur = float(data.get("spend_eur") or 0.0)

    def save(self, next_city_index: int, spend_eur: float) -> None:
        self.month = date.today().strftime("%Y-%m")
        self.next_city_index = next_city_index
        self.spend_eur = spend_eur
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump({
                    "month": self.month,
                    "next_city_index": next_city_index,
                    "spend_eur": spend_eur,
                }, f)
        except Exception:
            pass  # fail-soft : la persistance échoue -> le run continue quand même


class PlacesArchiConnector(Connector):
    """Balayage Places par ville (population décroissante) pour la
    population 'architecte'. `fetch` reprend au `checkpoint.next_city_index`
    et s'arrête dès que le budget € dur est atteint (fail-soft, jamais
    d'exception)."""

    name = "places"

    def __init__(self) -> None:
        self.last_total_count = 0
        self.spend_eur = 0.0
        self.cities_done = 0
        self.next_city_index = 0

    def fetch(
        self,
        cities: int = 100,
        budget_eur: float = 10.0,
        max_pages: int = 3,
        api_post: Optional[Callable[..., Dict[str, object]]] = None,
        checkpoint: Optional[CityCheckpoint] = None,
        **_: Any,
    ) -> List[Dict[str, Any]]:
        checkpoint = checkpoint or CityCheckpoint()
        spend = checkpoint.spend_eur
        idx = checkpoint.next_city_index
        villes = VILLES_FR[:cities]
        seen_ids: Set[str] = set()
        out: List[Dict[str, Any]] = []
        cities_done = 0
        # True dès qu'un appel n'a PAS pu être facturé (pas de clé Google,
        # ou exception réseau avalée par `search_places_text` -> `billed=
        # False`) : on arrête tout le balayage PROPREMENT sans avancer
        # `idx`/`cities_done` sur la ville en cours (elle n'a pas été
        # traitée), pour ne jamais épuiser le checkpoint mensuel à tort sur
        # un run qui n'a rien coûté ni rien rapporté (cf. revue B2/T3).
        halted = False

        while idx < len(villes) and spend + EUR_PER_CALL <= budget_eur:
            nom, _cp, _pop = villes[idx]
            for template in QUERIES:
                if halted or spend + EUR_PER_CALL > budget_eur:
                    break
                query = template.format(ville=nom)
                token: Optional[str] = None
                for _page in range(max(max_pages, 1)):
                    if spend + EUR_PER_CALL > budget_eur:
                        break
                    places, token, billed = search_places_text(
                        query, api_post=api_post, page_token=token)
                    if not billed:
                        halted = True
                        break
                    spend += EUR_PER_CALL
                    for p in places:
                        pid = p.get("id")
                        pname = p.get("name") or ""
                        if not pid or pid in seen_ids or not _archi_ok(pname):
                            continue
                        seen_ids.add(pid)
                        rec = dict(p)
                        rec.update({
                            "place_id": pid,
                            "formatted": p.get("address"),
                            "city": nom,
                            "hospitality": _hospitality(pname),
                        })
                        out.append(rec)
                    if not token:
                        break
                if halted:
                    break
            if halted:
                break
            idx += 1
            cities_done += 1

        self.last_total_count = len(out)
        self.spend_eur = spend
        self.cities_done = cities_done
        self.next_city_index = idx
        checkpoint.save(next_city_index=idx, spend_eur=spend)
        return out

    def to_candidates(self, records: List[Dict[str, Any]]) -> List[LeadCandidate]:
        today = date.today()
        out: List[LeadCandidate] = []
        for r in records:
            name = (r.get("name") or "").strip()
            if not name:
                continue
            secondary = ["annuaire places"]
            if r.get("hospitality"):
                secondary.append("portfolio hospitality/CHR")  # tier T2 (sonde)
            place_id = r.get("place_id") or ""
            proof = "Fiche Google Places (balayage volume, requête ville)."
            proof_url = (f"https://www.google.com/maps/place/?q=place_id:{place_id}"
                         if place_id else "")
            out.append(LeadCandidate(
                source="places",
                source_ref=f"places:{place_id}",
                establishment_name=name,
                city=r.get("city") or "",
                address=r.get("formatted") or "",
                main_signal="prescripteur actif",
                secondary_signals=secondary,
                lifecycle_label="unknown",
                population="architecte",
                establishment_type="architecte d'intérieur",
                website=r.get("website"),
                detection_date=today,
                classification_text=name,
                proof_text=proof,
                proof_url=proof_url,
                raw={"phone": r.get("phone")},
            ))
        return out
