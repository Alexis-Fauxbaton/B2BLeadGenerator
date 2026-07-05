# backend/app/ingestion/eval/match_eval.py
"""Éval du matching Insta -> SIREN sur les snapshots figés (CLI).

  python -m app.ingestion.eval.match_eval            # offline (fixtures HTTP)
  python -m app.ingestion.eval.match_eval --record   # live + écrit les fixtures
  python -m app.ingestion.eval.match_eval --live     # live sans fixtures

HTTP (Sirene/BAN) figé en fixtures ; l'arbitre LLM tourne live si clé présente
(température 0), comme l'éval de classification. Gates : 0 faux merge, rappel
des matchs attendus affiché (référence : 9 attendus au 2026-07-04).
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, Optional

from ..enrichment import siret_matcher as sm
from .run import load_groundtruth, load_snapshot

ROOT = Path(__file__).resolve().parent
FIX_DIR = ROOT / "fixtures" / "match"

# Ville probable par handle (ce que discover/locationName fournirait en prod).
CITY_HINTS = {
    "tregusto_sartrouville": "Sartrouville",
    "brasseriedelafontainelourmarin": "Lourmarin",
    "imagine.trouville": "Trouville-sur-Mer",
    "monica_stgermain": "Paris",
    "giorgina_restaurant": "Paris",
    "lartemise_colmar": "Colmar",
    "lemourerouge_cannes": "Cannes",
    "lamerpaulettetrouville": "Trouville-sur-Mer",
    "villa.henriette_cabourg": "Cabourg",
    "cafe_mokaparis": "Paris",
    "cherescousinesbagels": "Paris",
    "lemarcchiato": "Vienne",
}

_ADDR_RE = re.compile(
    r"\b\d{1,4}\s?(?:bis|ter)?\s?,?\s+(?:rue|avenue|av\.?|boulevard|bd\.?|place|"
    r"quai|chemin|all[ée]e|impasse|cours|route|passage|promenade)\s+"
    r"[a-zA-ZÀ-ÿ'’\- ]{3,45}", re.IGNORECASE)


def _key(url: str, params: Dict[str, Any]) -> str:
    return f"{url}?{json.dumps(params, sort_keys=True, ensure_ascii=False)}"


def _recording_fetch(store: Dict[str, Any]):
    def fetch(url, params):
        data = sm._http_get(url, params)
        store[_key(url, params)] = data
        return data
    return fetch


def _replay_fetch(store: Dict[str, Any]):
    def fetch(url, params):
        return store.get(_key(url, params), {})
    return fetch


def _inputs_from_snapshot(handle: str, snap: dict) -> Dict[str, Optional[str]]:
    """Reconstruit les entrées que le pipeline aurait : nom, ville, adresse, bio."""
    ba = snap.get("businessAddress") or {}
    ba_city = (ba.get("city_name") or "").split(",")[0].strip() or None
    addr = None
    if ba.get("street_address"):
        addr = ", ".join(filter(None, [ba.get("street_address"),
                                       ba.get("zip_code"), ba_city]))
    else:
        bio = snap.get("biography") or ""
        caps = " ".join((p.get("caption") or "") for p in (snap.get("latestPosts") or [])[:8])
        found = _ADDR_RE.findall(bio) or _ADDR_RE.findall(caps)
        city = CITY_HINTS.get(handle) or ba_city or ""
        if found:
            addr = f"{found[0]} {city}".strip()
    bio = (snap.get("biography") or "")[:300]
    ctx_caps = " | ".join(
        (p.get("caption") or "")[:100]
        for p in (snap.get("latestPosts") or [])[:4] if p.get("caption")
    )
    return {
        "name": snap.get("fullName") or handle,
        "city": CITY_HINTS.get(handle) or ba_city,
        "address": addr,
        "context": f"{bio} | posts: {ctx_caps}"[:600] if ctx_caps else bio,
    }


def run_match_eval(mode: str = "offline") -> dict:
    rows = load_groundtruth()
    results = []
    for row in rows:
        handle = row["handle"].strip()
        expected = (row.get("expected_siren") or "").strip() or None
        snap = load_snapshot(handle)
        if snap is None:
            results.append({"handle": handle, "status": "no_snapshot",
                            "expected": expected, "got": None})
            continue
        fix_path = FIX_DIR / f"{handle}.json"
        if mode == "record":
            store: Dict[str, Any] = {}
            fetch = _recording_fetch(store)
        elif mode == "live":
            fetch = sm._http_get
        else:
            if not fix_path.exists():
                results.append({"handle": handle, "status": "no_fixture",
                                "expected": expected, "got": None})
                continue
            fetch = _replay_fetch(json.loads(fix_path.read_text(encoding="utf-8")))
        inputs = _inputs_from_snapshot(handle, snap)
        got = sm.match(fetch=fetch, **inputs)
        if mode == "record":
            FIX_DIR.mkdir(parents=True, exist_ok=True)
            fix_path.write_text(json.dumps(store, ensure_ascii=False, indent=1),
                                encoding="utf-8")
        got_siren = got.siren if got else None
        if expected and got_siren == expected:
            status = "ok_match"
        elif expected and got_siren is None:
            status = "missed"
        elif expected:
            status = "wrong_siren"
        elif got_siren:
            status = "false_merge"
        else:
            status = "ok_nomatch"
        results.append({"handle": handle, "status": status, "expected": expected,
                        "got": got_siren,
                        "method": got.method if got else None,
                        "confidence": got.confidence if got else None})

    n_expected = sum(1 for r in results if r["expected"])
    ok = sum(1 for r in results if r["status"] == "ok_match")
    false_merges = [r for r in results if r["status"] in ("false_merge", "wrong_siren")]
    return {"results": results, "n_expected": n_expected, "ok": ok,
            "false_merges": false_merges}


def main() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT.parents[2] / ".env")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="Éval matching Insta -> SIREN")
    parser.add_argument("--record", action="store_true")
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    mode = "record" if args.record else ("live" if args.live else "offline")
    rep = run_match_eval(mode)
    print("=" * 64)
    print(f"EVAL MATCHING ({mode}) — {rep['ok']}/{rep['n_expected']} matchs attendus retrouvés")
    print("=" * 64)
    for r in rep["results"]:
        print(f'  {r["status"]:<12} {r["handle"]:<32} attendu={r["expected"] or "-":<11}'
              f' obtenu={r["got"] or "-":<11} ({r["method"] or ""})')
    if rep["false_merges"]:
        print(f'\n!! FAUX MERGES ({len(rep["false_merges"])}) — GATE ROUGE, à corriger avant de continuer')
    print("=" * 64)


if __name__ == "__main__":
    main()
