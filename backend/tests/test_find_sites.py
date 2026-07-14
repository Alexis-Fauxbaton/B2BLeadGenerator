"""Tests du CLI de découverte de site (``find_sites.py``), patron
``test_enrich_phones.py`` / ``test_run_prescripteurs_cli`` : ciblage SQL pur,
``--dry-run`` non destructif (JSONL parsable, AUCUNE écriture DB), ``--apply``
qui n'écrit ``website`` QUE sur les fiches ``found`` sans site existant,
stats cohérentes. Zéro réseau (``fetch`` factice)."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Callable, Dict, List, Optional
from urllib.parse import quote, urlparse

from sqlmodel import Session, SQLModel, create_engine, select

from app.ingestion.find_sites import SiteStats, _site_targets, run_find_sites
from app.models import Opportunity

FIXTURES = Path(__file__).parent / "fixtures" / "site_finder"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _engine():
    e = create_engine("sqlite://")
    SQLModel.metadata.create_all(e)
    return e


def _mk_opp(**overrides) -> Opportunity:
    base = dict(
        establishment_name="Atelier Dupont", establishment_type="architecte d'intérieur",
        city="Lyon", address="12 rue de la Republique, 69001 Lyon",
        main_signal="prescripteur actif", detection_date=date(2026, 7, 10),
        estimated_timing="J-90", source="sirene_stock", population="architecte",
        dirigeants=["Chiara Rossi, Gérante"], siren="123456789", siret="12345678900012",
        website=None,
    )
    base.update(overrides)
    return Opportunity(**base)


def _ddg_html(urls: List[str]) -> str:
    anchors = "".join(
        f'<a rel="nofollow" class="result__a" '
        f'href="//duckduckgo.com/l/?uddg={quote(u, safe="")}&amp;rut=x">titre</a>'
        for u in urls
    )
    return f"<html><body>{anchors}</body></html>"


def _dispatch_fetch(
    routes: Dict[str, str], pages: Dict[str, str],
) -> Callable[[str], Optional[str]]:
    """``fetch`` factice multi-fiches : route les requêtes DDG selon un
    fragment de la query normalisée (``+`` entre mots), sert les pages
    candidates par domaine (sans www). Zéro réseau."""
    def fetch(url: str) -> Optional[str]:
        if "html.duckduckgo.com" in url:
            for frag, html in routes.items():
                if frag in url:
                    return html
            return None
        host = urlparse(url).netloc.lower()
        bare = host[4:] if host.startswith("www.") else host
        return pages.get(bare)
    return fetch


# --- _site_targets (pur, testable sans réseau) ------------------------------------


def test_site_targets_filters_population_source_and_missing_website():
    with Session(_engine()) as s:
        s.add(_mk_opp(source_ref="a", website=None, source="sirene_stock"))
        s.add(_mk_opp(source_ref="b", website="https://deja-un-site.fr", source="sirene_stock"))
        s.add(_mk_opp(source_ref="c", website=None, source="places"))
        s.add(_mk_opp(source_ref="d", website=None, population="chr", source="sirene_stock"))
        s.add(_mk_opp(source_ref="e", website="", source="sirene_stock"))
        s.commit()

        refs = {o.source_ref for o in _site_targets(s, "architecte", "sirene_stock", 500)}
        assert refs == {"a", "e"}

        # source=None -> toutes les sources de la population.
        refs_all = {o.source_ref for o in _site_targets(s, "architecte", None, 500)}
        assert refs_all == {"a", "c", "e"}


def test_site_targets_respects_limit():
    with Session(_engine()) as s:
        for i in range(5):
            s.add(_mk_opp(source_ref=f"x{i}", website=None))
        s.commit()
        rows = _site_targets(s, "architecte", "sirene_stock", 2)
        assert len(rows) == 2


# --- --dry-run : JSONL parsable, AUCUNE écriture dans opportunities --------------


def test_dry_run_writes_no_website_and_emits_parsable_jsonl(tmp_path):
    engine = _engine()
    with Session(engine) as s:
        s.add(_mk_opp(source_ref="a"))
        s.commit()

    fetch = _dispatch_fetch(
        {"atelier+dupont+lyon": _read("ddg_results.html")},
        {"atelier-dupont.fr": _read("site_match.html")},
    )
    out_path = tmp_path / "out.jsonl"
    with Session(engine) as s:
        stats = run_find_sites(population="architecte", source="sirene_stock", limit=10,
                               apply=False, out=str(out_path), session=s, fetch=fetch)

    assert stats.scanned == 1
    assert stats.found == 1

    lines = out_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])  # JSONL bien parsable
    assert row["verdict"] == "found"
    assert row["website"] == "https://atelier-dupont.fr/"
    assert row["establishment_name"] == "Atelier Dupont"

    with Session(engine) as s2:
        opp = s2.exec(select(Opportunity).where(Opportunity.source_ref == "a")).one()
        assert not opp.website  # dry-run : la base n'est JAMAIS touchée


# --- --apply : écrit website UNIQUEMENT sur les 'found', jamais d'écrasement ------


def test_apply_writes_website_only_on_found_never_overwrites(tmp_path):
    engine = _engine()
    with Session(engine) as s:
        s.add(_mk_opp(source_ref="found-me"))
        s.add(_mk_opp(source_ref="platform-only", establishment_name="Studio Fantome"))
        s.commit()

    fetch = _dispatch_fetch(
        {
            "atelier+dupont+lyon": _read("ddg_results.html"),
            "studio+fantome": _ddg_html(["https://www.instagram.com/x/"]),
        },
        {"atelier-dupont.fr": _read("site_match.html")},
    )

    with Session(engine) as s:
        stats = run_find_sites(population="architecte", source="sirene_stock", limit=10,
                               apply=True, out=str(tmp_path / "out.jsonl"),
                               session=s, fetch=fetch)

    assert stats.scanned == 2
    assert stats.found == 1
    assert stats.no_candidate == 1

    with Session(engine) as s2:
        found_opp = s2.exec(select(Opportunity).where(Opportunity.source_ref == "found-me")).one()
        assert found_opp.website == "https://atelier-dupont.fr/"

        no_cand_opp = s2.exec(
            select(Opportunity).where(Opportunity.source_ref == "platform-only")
        ).one()
        assert not no_cand_opp.website  # aucun site attribué (VIDE > FAUX)


def test_apply_never_overwrites_existing_website():
    engine = _engine()
    with Session(engine) as s:
        # Site déjà présent -> hors cible de `_site_targets` (jamais retouché).
        s.add(_mk_opp(source_ref="already", website="https://deja-un-site.fr"))
        s.commit()

    fetch = _dispatch_fetch(
        {"atelier+dupont+lyon": _read("ddg_results.html")},
        {"atelier-dupont.fr": _read("site_match.html")},
    )
    with Session(engine) as s:
        stats = run_find_sites(population="architecte", source="sirene_stock", limit=10,
                               apply=True, session=s, fetch=fetch)
    assert stats.scanned == 0  # jamais ciblée : website déjà rempli

    with Session(engine) as s2:
        opp = s2.exec(select(Opportunity).where(Opportunity.source_ref == "already")).one()
        assert opp.website == "https://deja-un-site.fr"


# --- Stats cohérentes --------------------------------------------------------------


def test_stats_scanned_equals_sum_of_verdicts(tmp_path):
    engine = _engine()
    with Session(engine) as s:
        s.add(_mk_opp(source_ref="found-me"))
        s.add(_mk_opp(source_ref="no-candidate", establishment_name="Studio Fantome"))
        s.add(_mk_opp(source_ref="locked-out", establishment_name="Studio Homonyme"))
        s.commit()

    fetch = _dispatch_fetch(
        {
            "atelier+dupont+lyon": _read("ddg_results.html"),
            "studio+fantome": _ddg_html(["https://www.instagram.com/x/"]),
            "studio+homonyme": _ddg_html(["https://atelier-dupont-homonyme.fr/"]),
        },
        {
            "atelier-dupont.fr": _read("site_match.html"),
            "atelier-dupont-homonyme.fr": _read("site_homonym_othercity.html"),
        },
    )
    with Session(engine) as s:
        stats: SiteStats = run_find_sites(
            population="architecte", source="sirene_stock", limit=10,
            apply=False, out=str(tmp_path / "out.jsonl"), session=s, fetch=fetch,
        )

    assert stats.scanned == 3
    assert stats.scanned == (stats.found + stats.locked_out + stats.no_candidate
                             + stats.search_unavailable + stats.errors)
    assert stats.found == 1
    assert stats.no_candidate == 1
    assert stats.locked_out == 1
    assert stats.search_unavailable == 0
    assert stats.errors == 0
