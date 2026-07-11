"""HTTP POLI et injectable, partagé par les connecteurs d'annuaire (A2).

Throttle 2,5 s, User-Agent honnête, fail-soft. `polite_get` est le défaut réseau ;
les tests injectent un `HtmlFetch` factice alimenté par les HTML sondés."""
from __future__ import annotations

import time
from typing import Callable, Optional

import requests

# URL -> HTML texte, ou None (page illisible / interdite / erreur réseau).
HtmlFetch = Callable[[str], Optional[str]]

USER_AGENT = "Ambient Home lead research (contact: alexis.fauxbaton@gmail.com)"
_MIN_INTERVAL = 2.5  # scraping poli : >= 2,5 s entre deux requêtes
_last_call = [0.0]


def polite_get(url: str) -> Optional[str]:
    """GET throttlé (2,5 s), User-Agent honnête, fail-soft None."""
    wait = _MIN_INTERVAL - (time.monotonic() - _last_call[0])
    if wait > 0:
        time.sleep(wait)
    _last_call[0] = time.monotonic()
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
        resp.raise_for_status()
        return resp.text
    except Exception:
        return None
