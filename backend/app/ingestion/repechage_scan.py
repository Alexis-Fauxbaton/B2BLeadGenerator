"""CLI de BALAYAGE — repêchage des AMBIGUS du stock 74.10Z (Brique C, phase scan).

Contexte (sonde `.superpowers/sdd/sonde-volume/`, decision C) : le filtre v2
strict (`jeunes_studios.qualifies`, actuel) a durci le filtre v1 LARGE
historique (commit bcdfc41, avant aa206e5) par prudence — VIDE > FAUX. Un
"AMBIGU" est une denomination 74.10Z qui passait v1 mais que v2 rejette SANS
etre un faux-ami confirme (HARD_NEG/WORD_NEG) : un token FAIBLE seul
(design/studio/atelier/deco/concept/agencement) sans marqueur intérieur
(interieur/interior/home/espace/archi). Ce residu (~16/31 « X DESIGN »/
« X STUDIO » purs + « agencement » sans « interieur » du calibrage GT) est
le « gris » plausible a re-verifier au telephone/site (Phase 2, PAS ce CLI).

Ce module :
  - re-balaie le stock actif NAF 74.10Z + 71.11Z via `insee.fetch_stock_
    etablissements` (source retenue par la sonde — `recherche-entreprises`
    plafonne a 10 000 resultats, inexploitable a l'echelle ~450k) ;
  - isole les AMBIGUS parmi les 74.10Z (le 71.11Z reste HORS-CIBLE, cf.
    `sirene_stock.qualifies_71` — architecture batiment, VIDE > FAUX,
    jamais un ambigu) en reconstruisant le filtre v1 LARGE historique
    (`_v1`, tuples figes par la sonde) et en categorisant la raison exacte
    du rejet v2 (`_v2_rejection_reason`, reutilise TEL QUEL les constantes
    de `jeunes_studios.qualifies` — HARD_NEG/WORD_NEG/INTERIOR_MARKERS —
    sans reecrire cette logique) ;
  - stocke chaque ambigu dans un MAGASIN SEPARE (`AmbiguStore`, sqlite3 nu,
    fichier `data/stock_ambigus.db` — JAMAIS `chr_signal_radar.db`, dont
    l'unique ecrivain est pris par un autre grind, cf. regle absolue
    ecrivain) ; PK = siret -> reprise idempotente meme si le checkpoint
    n'a pas ete persiste (re-fetch tolere, INSERT OR IGNORE) ;
  - deduplique en LECTURE SEULE contre les opportunities existantes
    (`chr_signal_radar.db`, index de siren precharge UNE fois) : un siren
    deja en base ne devient jamais un ambigu ;
  - checkpoint persistant (`{cursor, done}` par cle de departement) pour
    reprendre un balayage interrompu (crash, veille PC) sans tout rejouer.

Choix du magasin — sqlite3 NU (pas SQLModel) : (1) isolation totale de
`chr_signal_radar.db` (fichier different, aucun engine partage, aucun risque
de toucher l'ecrivain pris) ; (2) contrainte UNIQUE(siret) native = dedup
intra-magasin gratuite sur reprise (pas de relecture JSONL a chaque insert
pour verifier les doublons) ; (3) schema simple et fige (une table de faits
plus une table de checkpoint), pas besoin du framework de migration du
modele principal ; (4) requetable directement (SQL) pour la Phase 2 de
verification (futur CLI, PAS celui-ci) sans reparser un fichier plat.
JSONL aurait ete plus simple a diffuser mais moins robuste a la reprise
(dedup = O(n) par ligne relue) et moins pratique a interroger.

Usage :
    python -m app.ingestion.repechage_scan --limit 5000 \\
        [--departments 75,92 | --departments france] \\
        [--store data/stock_ambigus.db] [--cursor "*"]
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import unicodedata
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Set

from sqlmodel import Session, select

from ..database import engine as main_engine
from ..models import Opportunity
from . import jeunes_studios as js
from .insee import fetch_stock_etablissements
from .sirene_delta import _address, _nd, _ymd
from .sirene_stock import STOCK_NAF_CODES

DEFAULT_STORE_PATH = "data/stock_ambigus.db"

# --- Filtre v1 LARGE historique (commit bcdfc41, avant durcissement aa206e5) ---
# Tuples FIGES par la sonde : reconstruction fidele, ne PAS aligner sur v2.
V1_POS = ("interieur", "design", "studio", "agencement", "deco", "archi",
          "atelier", "concept", "home", "espace")
V1_NEG = ("graphique", "graphic", "graphik", "graphisme", "web", "ux", "ui",
          "packaging", "motion")

# Raisons de rejet v2 qui restent des AMBIGUS (token faible sans marqueur —
# le « gris »). Toute autre raison (hard_neg/word_neg/vide_ou_nd) est un
# faux-ami CONFIRME ou un enregistrement inqualifiable : jamais un ambigu
# (VIDE > FAUX, doctrine inchangee).
AMBIGU_REASONS = frozenset({"sans_marqueur_interieur", "agencement_sans_interieur"})


def _norm(text: Optional[str]) -> str:
    """Duplique volontairement `jeunes_studios._norm`/`sirene_stock._norm`
    (convention du repo : petit helper prive re-decline par module plutot
    qu'importe depuis un module voisin)."""
    t = unicodedata.normalize("NFD", (text or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def _best_name(etab: Dict[str, Any]) -> Optional[str]:
    """Duplique `sirene_delta._best_name` (import direct impossible : nom
    prive convention-only, mais la logique est identique — enseigne >
    denomination usuelle > denomination UL > prenom+nom)."""
    per = (etab.get("periodesEtablissement") or [{}])[0]
    ul = etab.get("uniteLegale") or {}
    for cand in (
        per.get("enseigne1Etablissement"),
        per.get("denominationUsuelleEtablissement"),
        ul.get("denominationUniteLegale"),
    ):
        if _nd(cand):
            return _nd(cand)
    prenom, nom = _nd(ul.get("prenom1UniteLegale")), _nd(ul.get("nomUniteLegale"))
    if prenom and nom:
        return f"{prenom.title()} {nom.title()}"
    return None


def _v1(name: Optional[str]) -> bool:
    """True si `name` passe le filtre v1 LARGE historique (pre-aa206e5).
    PURE."""
    n = _norm(name)
    if not n or n == "[nd]":
        return False
    if any(neg in n for neg in V1_NEG):
        return False
    return any(kw in n for kw in V1_POS)


def _v2_rejection_reason(name: Optional[str]) -> str:
    """Categorise la raison EXACTE du rejet par `jeunes_studios.qualifies`
    (reutilise les MEMES constantes — HARD_NEG/WORD_NEG/INTERIOR_MARKERS —
    dans le MEME ordre que `qualifies`, sans reecrire son verdict : sert
    uniquement a nommer la branche qui a fait echouer `qualifies`, pour le
    champ « raison du rejet v2 » stocke). PURE.

    Valeurs possibles : 'vide_ou_nd', 'hard_neg', 'word_neg',
    'agencement_sans_interieur', 'sans_marqueur_interieur', ou 'qualifie'
    (ne devrait jamais atteindre un appelant `evaluate_ambigu` — `qualifies`
    vaudrait alors True et le record n'est pas un ambigu)."""
    n = _norm(name)
    if not n or n == "[nd]":
        return "vide_ou_nd"
    if any(neg in n for neg in js.HARD_NEG):
        return "hard_neg"
    if any(re.search(r"\b" + w + r"\b", n) for w in js.WORD_NEG):
        return "word_neg"
    if "agencement" in n and "interieur" not in n:
        return "agencement_sans_interieur"
    if not any(m in n for m in js.INTERIOR_MARKERS):
        return "sans_marqueur_interieur"
    return "qualifie"


@dataclass
class AmbiguRecord:
    """Ambigu stocke : denomination 74.10Z qui passe v1 LARGE mais que v2
    rejette sans etre un faux-ami confirme. Champs utiles a la Phase 2
    (verification, futur CLI — PAS celui-ci)."""
    siret: str
    siren: Optional[str]
    denomination: str
    ville: str
    cp: str
    adresse: str
    dirigeant: Optional[str]
    naf: str
    date_creation: Optional[str]  # ISO, ou None
    raison_rejet_v2: str
    detection_date: str  # ISO — date du scan (pas de creation)


def evaluate_ambigu(etab: Dict[str, Any], today: date) -> Optional[AmbiguRecord]:
    """Etablissement INSEE (stock) -> AmbiguRecord, ou None si : ferme, hors
    74.10Z (le 71.11Z reste hors-cible, cf. module docstring), denomination
    masquee/absente, rejete DEJA par le filtre v1 LARGE (un vrai rejet, pas
    un ambigu), qualifie par v2 (`jeunes_studios.qualifies` — devient une
    opportunite normale via `sirene_stock`, pas un ambigu), ou rejete par v2
    pour une raison DURE (hard_neg/word_neg — faux-ami confirme, VIDE >
    FAUX). Fonction PURE (aucun reseau, aucune DB)."""
    per = (etab.get("periodesEtablissement") or [{}])[0]
    if (per.get("etatAdministratifEtablissement") or "A") != "A":
        return None
    naf = per.get("activitePrincipaleEtablissement")
    if naf != "74.10Z":
        return None
    name = _best_name(etab)
    if not name:
        return None
    if not _v1(name):
        return None
    if js.qualifies(name):
        return None  # deja qualifie v2 -> pas un ambigu (deja une opportunite)
    reason = _v2_rejection_reason(name)
    if reason not in AMBIGU_REASONS:
        return None  # hard_neg/word_neg/vide_ou_nd -> faux-ami confirme

    created = _ymd(etab.get("dateCreationEtablissement"))
    address, city = _address(etab)
    cp = ((etab.get("adresseEtablissement") or {}).get("codePostalEtablissement") or "").strip()

    ul = etab.get("uniteLegale") or {}
    prenom, nom = _nd(ul.get("prenom1UniteLegale")), _nd(ul.get("nomUniteLegale"))
    dirigeant = f"{prenom.title()} {nom.title()}" if (prenom and nom) else None

    return AmbiguRecord(
        siret=etab.get("siret") or "",
        siren=etab.get("siren"),
        denomination=name,
        ville=city or "",
        cp=cp,
        adresse=address,
        dirigeant=dirigeant,
        naf=naf,
        date_creation=created.isoformat() if created else None,
        raison_rejet_v2=reason,
        detection_date=today.isoformat(),
    )


class AmbiguStore:
    """Magasin SEPARE (sqlite3 nu, jamais `chr_signal_radar.db`). PK siret
    -> `INSERT OR IGNORE` = dedup intra-magasin gratuite sur reprise.
    Checkpoint par cle de balayage (`{cursor, done}`)."""

    def __init__(self, path: str = DEFAULT_STORE_PATH) -> None:
        self.path = path
        self._conn = sqlite3.connect(path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS ambigus (
                siret TEXT PRIMARY KEY,
                siren TEXT,
                denomination TEXT,
                ville TEXT,
                cp TEXT,
                adresse TEXT,
                dirigeant TEXT,
                naf TEXT,
                date_creation TEXT,
                raison_rejet_v2 TEXT,
                detection_date TEXT
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS checkpoint (
                key TEXT PRIMARY KEY,
                cursor TEXT NOT NULL,
                done INTEGER NOT NULL DEFAULT 0
            )
        """)
        # Table de la phase VERIFICATION (`repechage_verify.py`, PAS ce module) :
        # un verdict par siret ambigu -- 'confirme' (site trouve ET marqueurs
        # interieur), 'infirme' (site trouve mais hors-cible), 'sans_site'
        # (aucun site decouvert, ou site injoignable au re-fetch -- REESSAYABLE,
        # cf. `list_unverified`). Meme magasin sqlite SEPARE que `ambigus`
        # (jamais chr_signal_radar.db), table distincte pour ne rien retoucher
        # au schema de la phase scan.
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS verify_verdicts (
                siret TEXT PRIMARY KEY,
                verdict TEXT NOT NULL,
                website TEXT,
                marqueurs TEXT,
                detail TEXT,
                verified_date TEXT NOT NULL
            )
        """)
        self._conn.commit()

    def save_candidate(self, rec: AmbiguRecord) -> bool:
        """Insere `rec`. True si nouvellement insere, False si le siret est
        deja present (dedup intra-magasin, idempotent sur reprise)."""
        if not rec.siret:
            return False
        cur = self._conn.execute(
            "INSERT OR IGNORE INTO ambigus "
            "(siret, siren, denomination, ville, cp, adresse, dirigeant, naf, "
            " date_creation, raison_rejet_v2, detection_date) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (rec.siret, rec.siren, rec.denomination, rec.ville, rec.cp,
             rec.adresse, rec.dirigeant, rec.naf, rec.date_creation,
             rec.raison_rejet_v2, rec.detection_date),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def get_checkpoint(self, key: str) -> "tuple[str, bool]":
        """-> (cursor, done). ('*', False) si aucun checkpoint pour `key`
        (premier balayage — curseur INSEE de depart)."""
        row = self._conn.execute(
            "SELECT cursor, done FROM checkpoint WHERE key = ?", (key,)
        ).fetchone()
        if not row:
            return "*", False
        return row[0], bool(row[1])

    def save_checkpoint(self, key: str, cursor: str, done: bool) -> None:
        self._conn.execute(
            "INSERT INTO checkpoint (key, cursor, done) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET cursor = excluded.cursor, "
            "done = excluded.done",
            (key, cursor, int(done)),
        )
        self._conn.commit()

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM ambigus").fetchone()
        return int(row[0]) if row else 0

    # --- Phase VERIFICATION (`repechage_verify.py`) -----------------------------

    def list_unverified(self, limit: Optional[int] = None) -> List["AmbiguRecord"]:
        """Ambigus SANS verdict de verification DEFINITIF -- jamais encore
        verifies (`verify_verdicts` absent) OU verifies `sans_site`
        (REESSAYABLE : aucun site trouve la derniere fois, mais un site peut
        apparaitre plus tard, cf. doctrine VIDE > FAUX). `confirme`/`infirme`
        sont DEFINITIFS -- jamais re-verifies. Ordre d'insertion (rowid),
        balayage deterministe et repartable, bornee a `limit` si fourni."""
        cols = ("siret", "siren", "denomination", "ville", "cp", "adresse",
                "dirigeant", "naf", "date_creation", "raison_rejet_v2",
                "detection_date")
        query = (
            "SELECT a." + ", a.".join(cols) + " FROM ambigus a "
            "LEFT JOIN verify_verdicts v ON v.siret = a.siret "
            "WHERE v.siret IS NULL OR v.verdict = 'sans_site' "
            "ORDER BY a.rowid"
        )
        params: "tuple[Any, ...]" = ()
        if limit is not None:
            query += " LIMIT ?"
            params = (limit,)
        rows = self._conn.execute(query, params).fetchall()
        return [AmbiguRecord(*row) for row in rows]

    def save_verdict(
        self, siret: str, verdict: str, website: Optional[str],
        marqueurs: List[str], detail: Optional[str], verified_date: str,
    ) -> None:
        """Ecrit/actualise le verdict de verification d'un ambigu (upsert,
        une seule ligne par siret -- reessayer un `sans_site` remplace la
        ligne precedente)."""
        self._conn.execute(
            "INSERT INTO verify_verdicts "
            "(siret, verdict, website, marqueurs, detail, verified_date) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(siret) DO UPDATE SET verdict = excluded.verdict, "
            "website = excluded.website, marqueurs = excluded.marqueurs, "
            "detail = excluded.detail, verified_date = excluded.verified_date",
            (siret, verdict, website, json.dumps(marqueurs, ensure_ascii=False),
             detail, verified_date),
        )
        self._conn.commit()

    def verdict_counts(self) -> Dict[str, int]:
        """Repartition des verdicts de verification deja stockes (compteur
        cumulatif du magasin, toutes reprises confondues)."""
        rows = self._conn.execute(
            "SELECT verdict, COUNT(*) FROM verify_verdicts GROUP BY verdict"
        ).fetchall()
        return {verdict: int(n) for verdict, n in rows}

    def close(self) -> None:
        self._conn.close()


@dataclass
class RepechageStats:
    """Compteurs d'un run de balayage."""
    departments_key: str = "france"
    fetched: int = 0            # enregistrements bruts recuperes (74.10Z + 71.11Z)
    ambigus_new: int = 0        # nouveaux ambigus inseres dans le magasin
    ambigus_duplicate: int = 0  # deja presents dans le magasin (reprise)
    deduped_existing: int = 0   # siren deja present dans chr_signal_radar.db (skip)
    naf_71_skipped: int = 0     # 71.11Z, hors-cible par construction
    not_ambigu: int = 0         # 74.10Z evalue mais pas un ambigu (deja qualifie/faux-ami)
    errors: int = 0
    done: bool = False          # stock epuise (curseurSuivant == curseur)
    next_cursor: str = ""


def _departments_key(departments: Optional[Sequence[str]]) -> str:
    if not departments:
        return "france"
    return ",".join(departments)


def _existing_sirens(session: Session) -> Set[str]:
    """Index de dedup — sirens deja presents dans `chr_signal_radar.db`
    (LECTURE SEULE, prechargee UNE fois par run, perf a l'echelle stock)."""
    rows = session.exec(select(Opportunity.siren).where(Opportunity.siren.is_not(None))).all()
    return {s for s in rows if s}


def run_repechage_scan(
    limit: int = 5000,
    departments: Optional[Sequence[str]] = None,
    store_path: str = DEFAULT_STORE_PATH,
    cursor: Optional[str] = None,
    store: Optional[AmbiguStore] = None,
    session: Optional[Session] = None,
    fetch: Optional[Any] = None,
) -> RepechageStats:
    """Balaie le stock actif NAF 74.10Z + 71.11Z (curseur INSEE, `limit`
    borne les enregistrements BRUTS de CE run), isole les AMBIGUS 74.10Z
    (`evaluate_ambigu`), deduplique en lecture seule contre
    `chr_signal_radar.db` (index de sirens preloade), et persiste chaque
    nouvel ambigu dans le magasin separe (commit PAR candidat). Checkpoint
    sauvegarde en fin de run (cle = `departments`) -> reprise au prochain
    appel via `--limit N` successifs.

    `cursor` force le curseur de depart (tests / reprise manuelle) — sinon
    lu depuis le checkpoint persiste. `store`/`session`/`fetch` injectables
    (tests sans reseau/DB reelle)."""
    dep_key = _departments_key(departments)
    own_store = store is None
    store = store or AmbiguStore(store_path)
    own_session = session is None
    session = session or Session(main_engine)
    stats = RepechageStats(departments_key=dep_key)

    try:
        start_cursor, already_done = store.get_checkpoint(dep_key)
        if cursor is not None:
            start_cursor = cursor
        if already_done and cursor is None:
            stats.done = True
            stats.next_cursor = ""
            return stats

        existing_sirens = _existing_sirens(session)

        meta: Dict[str, Any] = {}
        records, next_cursor = fetch_stock_etablissements(
            STOCK_NAF_CODES, cp_prefixes=departments, limit=limit,
            cursor=start_cursor, fetch=fetch, meta=meta,
        )
        stats.fetched = len(records)
        today = date.today()

        for etab in records:
            try:
                per = (etab.get("periodesEtablissement") or [{}])[0]
                naf = per.get("activitePrincipaleEtablissement")
                if naf == "71.11Z":
                    stats.naf_71_skipped += 1
                    continue
                rec = evaluate_ambigu(etab, today)
                if rec is None:
                    stats.not_ambigu += 1
                    continue
                if rec.siren and rec.siren in existing_sirens:
                    stats.deduped_existing += 1
                    continue
                inserted = store.save_candidate(rec)
                if inserted:
                    stats.ambigus_new += 1
                else:
                    stats.ambigus_duplicate += 1
            except Exception:
                stats.errors += 1

        stats.done = next_cursor == ""
        stats.next_cursor = next_cursor
        store.save_checkpoint(dep_key, next_cursor if next_cursor else start_cursor,
                              stats.done)
    finally:
        if own_session:
            session.close()
        if own_store:
            store.close()

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Balayage du stock 74.10Z+71.11Z -> repechage des AMBIGUS "
                    "(magasin separe, jamais chr_signal_radar.db)."
    )
    parser.add_argument("--limit", type=int, default=5000,
                        help="Enregistrements BRUTS max recuperes CE run (pas les ambigus).")
    parser.add_argument("--departments", default=None,
                        help="Prefixes de CP separes par virgule (ex. '75,92'), "
                             "ou omis = France entiere.")
    parser.add_argument("--store", default=DEFAULT_STORE_PATH,
                        help=f"Chemin du magasin sqlite separe (defaut {DEFAULT_STORE_PATH}).")
    parser.add_argument("--cursor", default=None,
                        help="Force le curseur de depart (sinon reprise via checkpoint).")
    args = parser.parse_args()

    departments = [d.strip() for d in args.departments.split(",") if d.strip()] \
        if args.departments else None

    print(f"Balayage repechage (limit={args.limit}, departments={departments or 'france'}, "
         f"store={args.store})...", file=sys.stderr)
    stats = run_repechage_scan(limit=args.limit, departments=departments,
                               store_path=args.store, cursor=args.cursor)
    print("[OK] Termine :", file=sys.stderr)
    for key, value in asdict(stats).items():
        print(f"   {key:<18} = {value}", file=sys.stderr)


if __name__ == "__main__":
    main()
