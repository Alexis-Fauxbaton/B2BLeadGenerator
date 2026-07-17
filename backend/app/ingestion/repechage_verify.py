"""CLI de VERIFICATION de la brique C (repechage des ambigus stock 74.10Z,
phase 2 -- suite de `repechage_scan.py`).

Contexte : `repechage_scan.py` isole les AMBIGUS (denominations 74.10Z qui
passaient le filtre v1 LARGE historique mais que le filtre v2 strict rejette
sans etre un faux-ami confirme -- « X DESIGN »/« X STUDIO »/« X CONCEPT » sans
marqueur interieur) dans un magasin sqlite SEPARE (`data/stock_ambigus.db`).
Ce module les VERIFIE un par un :

  1. decouverte de site via le moteur EXISTANT et INCHANGE
     (`enrichment.site_finder.find_site` -- verrou d'identite gate, devinette
     de domaine, dirigeants -- importe seulement, jamais modifie ni reecrit) ;
  2. si un site est trouve, MARQUEURS INTERIEUR sur son CONTENU
     (`evaluate_site_content`) : reutilise les gardes negatives dures de
     `jeunes_studios.qualifies` v2 (`HARD_NEG` -- design graphique/produit,
     evenementiel, maroquinerie...) ETENDUES de deux familles propres au
     CONTENU d'un site (absentes du filtre de DENOMINATION, car le 71.11Z est
     deja hors-cible en amont, cf. `repechage_scan`, mais un site peut le
     reveler alors que le nom seul 74.10Z ne le disait pas) : paysagisme
     (« architecte paysagiste », metier adjacent) et architecture BATIMENT
     pure (« permis de construire », « maison individuelle »). Exige ENSUITE
     >= 1 marqueur POSITIF (`jeunes_studios.INTERIOR_MARKERS`, reutilise TEL
     QUEL -- interieur/interior/home/espace/archi) dans le texte agrege
     title/h1/og:site_name (`site_finder.extract_identity_markers`, reutilise)
     + meta description + texte visible de la HOME DU DOMAINE RACINE ;
  3. verdict stocke dans le MEME magasin separe (`AmbiguStore.save_verdict`,
     table `verify_verdicts`, cf. `repechage_scan.py`) :
       - `confirme` : site trouve ET marqueur(s) interieur present(s) ;
       - `infirme`  : site trouve mais hors-cible (garde negative, ou aucun
                      marqueur positif) -- DEFINITIF, jamais re-verifie ;
       - `sans_site`: aucun site trouve (locked_out/no_candidate/
                      search_unavailable/error), OU site trouve par
                      `find_site` mais injoignable au RE-fetch de ce module --
                      REESSAYABLE (VIDE > FAUX : jamais un jugement definitif
                      sans avoir pu regarder le contenu).

CACHE `find_site` -- ATTENTION regle absolue ecrivain : `find_site` lit/ecrit
SYSTEMATIQUEMENT le cache de verdicts (table `handle_verdicts`) via la session
SQLModel qu'on lui passe. `chr_signal_radar.db` a UN SEUL ecrivain a la fois et
il est DEJA PRIS (grind en cours) -- on ne peut donc JAMAIS lui passer une
session sur cette base, meme en dry-run pur (cache = ecriture). Ce module lui
fournit donc une session sur un fichier sqlite SEPARE, DEDIE
(`data/repechage_cache.db` par defaut, cf. `_make_cache_session`) ou SEULE la
table `handle_verdicts` est creee (`create_all(tables=[...])`, jamais
`opportunities` ni le reste du schema principal) -- ecriture totalement
isolee, jamais vers la base principale. Les cles de cache (`sitefind:siren:
v<N>:<siren>`) sont deja PREFIXEES et VERSIONNEES par `site_finder` -- aucune
collision possible avec un futur usage de ce meme fichier de cache par
`find_sites.py` sur les VRAIES fiches (cles `sitefind:opp:v<N>:<id>`, jamais
`siren` puisque ces fiches ont un id).

`--apply` (integration des `confirme` dans `opportunities` via le pipeline
existant `source='sirene_stock'`, dedup/corroboration inchangees) N'EST PAS
implemente ICI -- reserve a un futur chantier (brief explicite : "plus tard,
pas toi"). `run_repechage_verify(apply=True)` refuse tout de suite
(`NotImplementedError`), avant tout travail -- jamais d'ecriture partielle
silencieuse dans `opportunities`. Le CLI reste DRY-RUN par defaut et unique
mode reellement fonctionnel.

Rapport JSONL par fiche (`VerifyResult` -- requetes, site, signaux, marqueurs)
: consommable tel quel par un futur echantillonneur GT (patron
`eval/stock_gt_sample.py`).

Usage :
    python -m app.ingestion.repechage_verify --limit 15 \\
        [--store data/stock_ambigus.db] [--cache data/repechage_cache.db] \\
        [--out chemin.jsonl] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import date
from html import unescape as _html_unescape
from typing import Any, Dict, List, Optional

from sqlmodel import Session, SQLModel, create_engine

from ..models import HandleVerdict
from . import jeunes_studios as js
from .enrichment.site_finder import (
    HtmlFetch,
    _domain,
    _fetch_home_via,
    _polite_get,
    extract_identity_markers,
    find_site,
)
from .repechage_scan import AmbiguRecord, AmbiguStore, DEFAULT_STORE_PATH

DEFAULT_CACHE_PATH = "data/repechage_cache.db"

# --- Marqueurs de CONTENU de site (distincts des marqueurs de DENOMINATION -----
#     de `repechage_scan`/`jeunes_studios`, meme doctrine VIDE > FAUX) ---------

# Gardes negatives DURES sur le CONTENU du site : reutilise TEL QUEL
# `jeunes_studios.HARD_NEG` (design graphique/produit, evenementiel,
# maroquinerie, jeu video...) + deux familles propres au CONTENU (jamais
# necessaires au niveau DENOMINATION puisque le 71.11Z est deja hors-cible en
# amont, cf. `repechage_scan` -- mais un site peut reveler une architecture
# BATIMENT pure ou un paysagisme que le nom seul 74.10Z ne disait pas).
SITE_HARD_NEG = js.HARD_NEG + (
    "graphik",  # variante orthographique de "graphic"/"graphique" (absente de HARD_NEG)
    "paysagiste", "paysager", "architecture paysagere", "architecte paysagiste",
    "permis de construire", "maison individuelle", "maitre d oeuvre batiment",
)

# Marqueurs POSITIFS : reutilise TEL QUEL `jeunes_studios.INTERIOR_MARKERS`
# (interieur/interior/home/espace/archi) -- meme doctrine, meme constante,
# jamais reecrite.
SITE_POSITIVE_MARKERS = js.INTERIOR_MARKERS

# Groupe (1) = le guillemet OUVRANT (simple ou double), reutilise en
# back-reference pour fermer l'attribut : une apostrophe FRANCAISE dans le
# contenu (« d'intérieur », « L'Atelier »… tres frequent) ne tronque plus
# l'extraction quand l'attribut est delimite par des guillemets DOUBLES
# (piege d'une classe de caracteres ``[^"\']`` qui exclurait aussi le simple
# guillemet -- volontairement evite ici).
_META_DESC_RE = re.compile(
    r'<meta[^>]+name=["\']description["\'][^>]+content=(["\'])(.*?)\1', re.I)
_META_DESC_RE2 = re.compile(
    r'<meta[^>]+content=(["\'])(.*?)\1[^>]+name=["\']description["\']', re.I)
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_TEXT_CAP = 20000  # plafond raisonnable (home lourde), evite un texte demesure


def _norm(text: Optional[str]) -> str:
    """Duplique volontairement `jeunes_studios._norm`/`repechage_scan._norm`
    (convention du repo : petit helper prive re-decline par module)."""
    t = unicodedata.normalize("NFD", (text or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def _visible_text(html: Optional[str]) -> str:
    """Texte visible d'une page HTML (scripts/styles retires, tags retires,
    entites HTML decodees), plafonne a :data:`_TEXT_CAP`. Petit helper duplique
    volontairement plutot que d'exposer une fonction privee supplementaire dans
    `site_finder` (meme convention que `_norm`). PURE."""
    if not html:
        return ""
    no_script = _SCRIPT_STYLE_RE.sub(" ", html)
    text = _TAG_RE.sub(" ", no_script)
    return _html_unescape(text)[:_TEXT_CAP]


def _meta_description(html: Optional[str]) -> str:
    """Contenu de `<meta name="description">`, chaine vide si absent. PURE."""
    if not html:
        return ""
    m = _META_DESC_RE.search(html) or _META_DESC_RE2.search(html)
    return _html_unescape(m.group(2)) if m else ""


def _aggregate_site_text(html: Optional[str]) -> str:
    """Texte agrege d'une page (title + h1 + og:site_name via
    `site_finder.extract_identity_markers`, reutilise TEL QUEL, + meta
    description + texte visible) -- passe a :func:`evaluate_site_content`.
    PURE."""
    if not html:
        return ""
    return " ".join((
        extract_identity_markers(html),
        _meta_description(html),
        _visible_text(html),
    ))


def evaluate_site_content(aggregated_text: str) -> "tuple[bool, List[str]]":
    """True + marqueurs positifs trouves si le texte agrege d'un site parle
    d'ARCHITECTURE/DECORATION D'INTERIEUR. False (marqueurs vides) si une garde
    negative (:data:`SITE_HARD_NEG`) est presente -- MEME en presence d'un
    marqueur positif (VIDE > FAUX, meme precedence que `jeunes_studios.
    qualifies` : le negatif l'emporte toujours). Sinon True SEULEMENT si >= 1
    marqueur de :data:`SITE_POSITIVE_MARKERS` co-occurre. PURE."""
    n = _norm(aggregated_text)
    if any(neg in n for neg in SITE_HARD_NEG):
        return False, []
    found = [m for m in SITE_POSITIVE_MARKERS if m in n]
    return (len(found) > 0), found


def _fetch_website_text(fetch: HtmlFetch, website: str) -> Optional[str]:
    """Re-fetch la HOME DU DOMAINE RACINE du site attribue par `find_site`
    (meme patron que `site_finder._inspect_candidate`, reutilise
    `_fetch_home_via` -- jamais reecrit) et l'agrege pour
    :func:`evaluate_site_content`. None si le domaine est illisible ou si la
    home ne repond plus au re-fetch (VIDE > FAUX -- verdict `sans_site`,
    REESSAYABLE, jamais `infirme` sur une simple panne reseau)."""
    domain = _domain(website)
    if not domain:
        return None
    html, _ = _fetch_home_via(fetch, "https://" + domain + "/")
    return _aggregate_site_text(html) if html else None


@dataclass
class _AmbiguOpp:
    """Adaptateur `AmbiguRecord` -> interface attendue par `site_finder.
    find_site` (qui lit `establishment_name`/`city`/`address`/`dirigeants`/
    `siren`/`siret`/`id` via `getattr`, patron `models.Opportunity`). `id=None`
    -- l'ambigu n'est PAS encore une fiche en base -- fait replier `find_site`
    sur la cle de cache `sitefind:siren:v<N>:<siren>` (jamais de collision avec
    les cles `sitefind:opp:v<N>:<id>` des vraies fiches)."""
    id: Optional[int]
    establishment_name: str
    city: str
    address: str
    siren: Optional[str]
    siret: Optional[str]
    dirigeants: List[str]


def _ambigu_to_opp(rec: AmbiguRecord) -> _AmbiguOpp:
    address = f"{rec.adresse}, {rec.cp} {rec.ville}".strip(", ").strip()
    return _AmbiguOpp(
        id=None, establishment_name=rec.denomination, city=rec.ville,
        address=address, siren=rec.siren, siret=rec.siret,
        dirigeants=[rec.dirigeant] if rec.dirigeant else [],
    )


@dataclass
class VerifyResult:
    """Trace COMPLETE d'une verification pour UN ambigu -- rapport JSONL
    (requetes/site/signaux/marqueurs), consommable par un futur echantillonneur
    GT (patron `eval/stock_gt_sample.py`)."""
    siret: str
    denomination: str
    queries: List[str] = field(default_factory=list)
    website: Optional[str] = None
    name_signal: Optional[str] = None  # A1_content | A2_domain | C_dirigeant | None
    corroboration: List[str] = field(default_factory=list)   # verrou B (site_finder)
    marqueurs: List[str] = field(default_factory=list)       # marqueurs interieur trouves
    site_finder_verdict: str = "no_candidate"
    verdict: str = "sans_site"    # confirme | infirme | sans_site
    detail: str = ""
    from_cache: bool = False


def verify_ambigu(
    rec: AmbiguRecord, cache_session: Session, fetch: HtmlFetch = _polite_get,
    today: Optional[date] = None,
) -> VerifyResult:
    """Verifie UN ambigu : decouverte de site (`find_site`, moteur EXISTANT
    INCHANGE) puis MARQUEURS INTERIEUR sur son contenu si un site est trouve.

    `confirme` : site trouve ET >= 1 marqueur interieur present.
    `infirme`  : site trouve mais garde negative OU aucun marqueur positif --
                 DEFINITIF (jamais re-verifie, cf. `AmbiguStore.list_unverified`).
    `sans_site`: aucun site trouve par `find_site` (locked_out/no_candidate/
                 search_unavailable/error), OU site trouve mais injoignable au
                 RE-fetch de CE module -- REESSAYABLE dans les deux cas, jamais
                 un jugement definitif sans avoir pu regarder le contenu (VIDE
                 > FAUX)."""
    today = today or date.today()
    opp = _ambigu_to_opp(rec)
    sf_result = find_site(opp, cache_session, fetch=fetch, today=today)

    out = VerifyResult(
        siret=rec.siret, denomination=rec.denomination,
        queries=sf_result.queries, website=sf_result.website,
        name_signal=sf_result.name_signal, corroboration=sf_result.corroboration,
        site_finder_verdict=sf_result.verdict, from_cache=sf_result.from_cache,
    )

    if sf_result.verdict != "found" or not sf_result.website:
        out.verdict = "sans_site"
        out.detail = sf_result.verdict
        return out

    text = _fetch_website_text(fetch, sf_result.website)
    if text is None:
        out.verdict = "sans_site"
        out.detail = "site_injoignable_au_re_fetch"
        return out

    ok, markers = evaluate_site_content(text)
    out.marqueurs = markers
    if ok:
        out.verdict = "confirme"
        out.detail = sf_result.name_signal or "site_marqueurs_interieur"
    else:
        out.verdict = "infirme"
        out.detail = "site_sans_marqueur_interieur"
    return out


def _make_cache_session(cache_path: str) -> Session:
    """Session vers le CACHE SEPARE des verdicts `find_site` (`handle_
    verdicts`), JAMAIS `chr_signal_radar.db` (regle absolue ecrivain -- son
    unique ecrivain est deja pris par un autre grind). `find_site` lit/ecrit
    SYSTEMATIQUEMENT ce cache via la session qu'on lui passe -- on lui fournit
    donc une session sur un fichier sqlite DEDIE (`data/repechage_cache.db` par
    defaut) ou SEULE la table `handle_verdicts` est creee
    (`create_all(tables=[HandleVerdict.__table__])`, jamais `opportunities` ni
    le reste du schema principal). Ecriture totalement isolee."""
    cache_engine = create_engine(
        f"sqlite:///{cache_path}", connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(cache_engine, tables=[HandleVerdict.__table__])
    return Session(cache_engine)


@dataclass
class VerifyStats:
    """Compteurs d'un run de verification."""
    scanned: int = 0
    confirme: int = 0
    infirme: int = 0
    sans_site: int = 0
    errors: int = 0


def _record_stats(stats: VerifyStats, verdict: str) -> None:
    if verdict == "confirme":
        stats.confirme += 1
    elif verdict == "infirme":
        stats.infirme += 1
    elif verdict == "sans_site":
        stats.sans_site += 1
    else:
        stats.errors += 1


def run_repechage_verify(
    limit: int = 50,
    store_path: str = DEFAULT_STORE_PATH,
    cache_path: str = DEFAULT_CACHE_PATH,
    apply: bool = False,
    out: Optional[str] = None,
    store: Optional[AmbiguStore] = None,
    cache_session: Optional[Session] = None,
    fetch: Optional[HtmlFetch] = None,
) -> VerifyStats:
    """Verifie jusqu'a `limit` ambigus du magasin (`AmbiguStore.
    list_unverified` -- jamais un `confirme`/`infirme` deja tranche, reessaye
    les `sans_site`). Persiste CHAQUE verdict dans le magasin separe (jamais
    `chr_signal_radar.db`) et emet un rapport JSONL par fiche (`out`, sinon
    stdout).

    `apply=True` N'EST PAS implemente ICI (integration des `confirme` dans
    `opportunities` via le pipeline existant `source='sirene_stock'` --
    reserve a un futur chantier) : refuse IMMEDIATEMENT, avant tout travail,
    jamais d'ecriture partielle silencieuse. Le mode reellement fonctionnel
    est le dry-run (`apply=False`, defaut)."""
    if apply:
        raise NotImplementedError(
            "repechage_verify --apply n'est pas implemente : l'integration des "
            "ambigus 'confirme' dans opportunities (pipeline existant "
            "source='sirene_stock', dedup/corroboration inchangees) est un "
            "futur chantier, hors perimetre de ce CLI. Utiliser --dry-run "
            "(defaut) -- il persiste deja les verdicts dans le magasin separe."
        )

    own_store = store is None
    store = store or AmbiguStore(store_path)
    own_cache = cache_session is None
    cache_session = cache_session or _make_cache_session(cache_path)
    fetch = fetch or _polite_get
    stats = VerifyStats()
    today = date.today()

    out_file = None
    try:
        if out:
            out_file = open(out, "w", encoding="utf-8")

        targets = store.list_unverified(limit=limit)
        for rec in targets:
            stats.scanned += 1
            try:
                result = verify_ambigu(rec, cache_session, fetch=fetch, today=today)
                cache_session.commit()  # persiste le cache handle_verdicts ecrit par find_site
                store.save_verdict(
                    rec.siret, result.verdict, result.website, result.marqueurs,
                    result.detail, today.isoformat(),
                )
                _record_stats(stats, result.verdict)

                line = json.dumps(asdict(result), ensure_ascii=False)
                if out_file is not None:
                    out_file.write(line + "\n")
                else:
                    print(line)
            except Exception:
                stats.errors += 1
                cache_session.rollback()
    finally:
        if out_file is not None:
            out_file.close()
        if own_cache:
            cache_session.close()
        if own_store:
            store.close()

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verification des AMBIGUS du magasin repechage (dry-run "
                    "par defaut ; --apply NON implemente, cf. docstring)."
    )
    parser.add_argument("--limit", type=int, default=50,
                        help="Nombre max d'ambigus verifies CE run (defaut 50).")
    parser.add_argument("--store", default=DEFAULT_STORE_PATH,
                        help=f"Chemin du magasin d'ambigus (defaut {DEFAULT_STORE_PATH}).")
    parser.add_argument("--cache", default=DEFAULT_CACHE_PATH,
                        help=f"Chemin du cache de verdicts find_site, SEPARE de "
                             f"chr_signal_radar.db (defaut {DEFAULT_CACHE_PATH}).")
    parser.add_argument("--out", default=None, help="Fichier JSONL de sortie (sinon stdout).")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true",
                      help="Verifie et persiste les verdicts dans le magasin "
                           "SEPARE, AUCUNE ecriture dans opportunities (defaut).")
    mode.add_argument("--apply", action="store_true",
                      help="NON implemente ici (integration opportunities, "
                           "futur chantier) -- leve NotImplementedError.")
    args = parser.parse_args()

    mode_label = "apply" if args.apply else "dry-run"
    print(f"Verification repechage (limit={args.limit}, store={args.store}, "
         f"cache={args.cache}, mode={mode_label})...", file=sys.stderr)
    stats = run_repechage_verify(limit=args.limit, store_path=args.store,
                                 cache_path=args.cache, apply=bool(args.apply),
                                 out=args.out)
    print("[OK] Termine :", file=sys.stderr)
    for key, value in asdict(stats).items():
        print(f"   {key:<10} = {value}", file=sys.stderr)


if __name__ == "__main__":
    main()
