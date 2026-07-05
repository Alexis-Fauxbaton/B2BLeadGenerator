"""Matching lead Insta -> SIREN/SIRET via le registre (brique 1 du pivot).

Remplace `backfill_siren`. Chaîne : nom nettoyé (auto-accept seulement si
cohérence géo) -> adresse (BAN -> /near_point) -> arbitre LLM sur candidats
ambigus. JAMAIS de merge nom-seul sans géo ni arbitre (piège Auréa : un
"AUREA" 56.10A existe à Théoule, la bio "bijoux, Portugal" doit le rejeter).
Fail-soft partout. Cf. docs/inventory-pivot-design.md.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Optional

# Mots génériques ignorés pour la concordance de nom (repris de backfill).
_GENERIC = {
    "le", "la", "les", "du", "de", "des", "et", "aux", "au", "chez", "paris",
    "cafe", "bar", "restaurant", "brasserie", "hotel", "resto", "bistro",
    "bistrot", "traiteur", "pizzeria", "boulangerie", "snack", "food", "coffee",
    "shop", "coffeeshop", "salon", "the",
}

# Séparateurs de décoration dans les fullName Insta ("NOM • VILLE", "NOM - VILLE").
_SEP_RE = re.compile(r"[|•\n–]| - |(?<=\w)-(?=[A-ZÀ-Ý])")
_NUM_RE = re.compile(r"\b(\d{1,4})\b")


def clean_name(raw: Optional[str]) -> str:
    """Nom Insta -> nom cherchable : NFKC (lettres stylisées -> ASCII), strip
    emojis/symboles, premier segment avant séparateur décoratif."""
    text = unicodedata.normalize("NFKC", raw or "")
    # S* = symboles/emojis, C* = contrôles, Mn = variation selectors (U+FE0F
    # après un emoji) — les accents français sont composés par NFKC, donc
    # retirer Mn ne les casse pas.
    text = "".join(c for c in text
                   if unicodedata.category(c)[0] not in ("S", "C")
                   and unicodedata.category(c) != "Mn")
    first = _SEP_RE.split(text)[0]
    return re.sub(r"\s+", " ", first).strip()


def street_number(address: Optional[str]) -> Optional[str]:
    """Premier numéro de voie d'une adresse (clé de comparaison ±exacte)."""
    m = _NUM_RE.search(address or "")
    return m.group(1) if m else None


def _tokens(text: Optional[str]) -> set:
    text = (text or "").lower()
    text = "".join(c for c in unicodedata.normalize("NFD", text)
                   if unicodedata.category(c) != "Mn")
    return {t for t in re.split(r"[^a-z0-9]+", text)
            if len(t) > 1 and t not in _GENERIC and not t.isdigit()}


def _name_overlap(ig_name: str, sirene_text: str) -> bool:
    """Au moins un token distinctif du nom Insta présent côté Sirene."""
    want = _tokens(ig_name)
    return bool(want) and bool(want & _tokens(sirene_text))
