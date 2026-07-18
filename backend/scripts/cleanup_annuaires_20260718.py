"""Script ONE-SHOT (dry-run par défaut) — nettoie 3 défauts qualité repérés
par Alexis sur les fiches annuaires (revue 2026-07-18) et déjà corrigés À LA
SOURCE (les PROCHAINES ingestions n'y sont plus exposées) :

  - pipeline._soft_dedup_same_connector          (défaut #1, doublon intra-annuaire)
  - annuaires.mon_architecte_interieur._is_hors_cible_maitre_oeuvre (défaut #2)
  - annuaires.monacomania._INTERIOR_MENTION_RE   (défaut #3, garde parse_card)

Ce script rattrape les fiches DÉJÀ EN BASE, créées AVANT ces fixes, en
réutilisant les MÊMES fonctions pures (tokenisation nom/ville, corroboration
tél/domaine, gardes hors-cible) plutôt que de réinventer une heuristique
parallèle :

  [1] DOUBLON INTRA-ANNUAIRE : même connecteur (`pipeline._connector_key`),
      même nom+ville normalisés (`siret_matcher._tokens`/`_city_tokens`), ET
      corroboration forte (même téléphone normalisé OU même domaine de site,
      `pipeline._norm_phone`/`siret_matcher._domain`) -> fusion. Garde la
      fiche au plus PETIT id (la plus ancienne), comble ses champs vides avec
      ceux du doublon, supprime le doublon (Signal + ContactHistory inclus).
      VIDE > FAUX : un groupe de 3+ fiches homonymes du même connecteur est
      laissé de côté (ambigu, hors scope d'une fusion automatique) — cas réel
      couvert : Sabrina Rosadoni #6788/#6790 (mon_architecte_interieur, même
      téléphone).
  [2] HORS-CIBLE MAÎTRE D'ŒUVRE (mon_architecte_interieur uniquement) :
      `establishment_name` dominé par « maître d'œuvre »/« maîtrise d'œuvre »/
      « constructeur »/« bureau d'études » SANS mention d'architecture ou de
      décoration (`_is_hors_cible_maitre_oeuvre` — la description originale de
      la fiche n'étant pas persistée en base, le test s'appuie sur le seul
      `establishment_name`, ce qui suffit sur le cas réel connu) -> purge. Cas
      réel : #6785 « Maître d'oeuvre Vigneux-de-Bretagne – Guillaume Clouet ».
  [3] HORS-CIBLE INTÉRIEUR (monacomania uniquement) : fiches SANS le tag
      « mention architecture d'intérieur » posé à l'ingestion par
      `to_candidates` (preuve stockée de la garde `_INTERIOR_MENTION_RE`
      appliquée au moment du parsing d'origine) -> purge. Cas réel : 5 des 6
      fiches monacomania (seule ARCH - FRED GENIN porte le tag).

Usage (depuis backend/, PYTHONIOENCODING=utf-8 recommandé) :

  python -m scripts.cleanup_annuaires_20260718            # LISTE seule (dry-run, lecture)
  python -m scripts.cleanup_annuaires_20260718 --apply     # applique fusions + purges

SYNCHRONE, DB seule (aucun réseau). `--apply` sauvegarde
`chr_signal_radar.db.bak-cleanupannuaires-<horodatage>` AVANT toute écriture
(même doctrine que les autres scripts one-shot, cf.
app/ingestion/eval/backfill_cfai_societe.py). Sans `--apply` : AUCUNE écriture
(pur SELECT), sûr à lancer même base occupée par un autre run en parallèle.

NE PAS lancer `--apply` tant que la base n'est pas libre (cf. mémoire :
« un grind écrit dans la base en parallèle ») — l'orchestrateur s'en charge."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Dict, List, Tuple

from sqlmodel import Session, col, delete, select

from app.ingestion.annuaires.mon_architecte_interieur import (
    _is_hors_cible_maitre_oeuvre,
)
from app.ingestion.enrichment.siret_matcher import _city_tokens, _domain, _tokens
from app.ingestion.pipeline import _connector_key, _norm_phone
from app.models import ContactHistory, Opportunity, Signal

MONACOMANIA_INTERIOR_TAG = "mention architecture d'intérieur"


@dataclass
class DuplicatePair:
    keep_id: int
    drop_id: int
    keep_ref: str
    drop_ref: str
    name: str
    city: str
    reason: str


@dataclass
class HorsCibleHit:
    id: int
    source_ref: str
    name: str
    reason: str


def _corroborates_pair(a: Opportunity, b: Opportunity) -> bool:
    """Corroboration FORTE entre deux fiches déjà en base (même téléphone
    normalisé OU même domaine de site) — signaux « forts » de
    `pipeline._corroborates`, réutilisés tels quels (`_norm_phone`/`_domain`).
    Pas de repli géo ici : les deux fiches sont déjà même nom+ville par
    construction de l'appelant (`find_intra_annuaire_duplicates`), un repli
    géo ajouterait un risque de faux merge sans information indépendante.
    PURE."""
    pa, pb = _norm_phone(a.phone), _norm_phone(b.phone)
    if pa and pa == pb:
        return True
    da, db = _domain(a.website), _domain(b.website)
    return bool(da) and da == db


def find_intra_annuaire_duplicates(session: Session) -> List[DuplicatePair]:
    """Défaut #1 : doublons INTRA-connecteur (même annuaire liste 2x la même
    personne sous 2 libellés). Groupe les fiches architecte par (connecteur,
    nom normalisé, ville normalisée) ; un groupe de taille EXACTEMENT 2, avec
    corroboration forte, devient une paire à fusionner. Lecture seule (aucune
    écriture) — sûr à appeler en mode LISTE."""
    rows = session.exec(
        select(Opportunity).where(Opportunity.population == "architecte")
    ).all()
    groups: Dict[Tuple[str, frozenset, frozenset], List[Opportunity]] = {}
    for o in rows:
        nt, ct = _tokens(o.establishment_name), _city_tokens(o.city)
        if not nt or not ct:
            continue
        key = (_connector_key(o.source, o.source_ref), frozenset(nt), frozenset(ct))
        groups.setdefault(key, []).append(o)

    pairs: List[DuplicatePair] = []
    for (connector, _, _), items in groups.items():
        if len(items) != 2:
            continue  # 1 fiche (rien à faire) ou 3+ (ambigu -- hors scope auto, VIDE > FAUX)
        a, b = sorted(items, key=lambda o: o.id)
        if not _corroborates_pair(a, b):
            continue  # homonymes sans signal commun -> jamais de faux merge
        pairs.append(DuplicatePair(
            keep_id=a.id, drop_id=b.id,
            keep_ref=a.source_ref or "", drop_ref=b.source_ref or "",
            name=a.establishment_name, city=a.city,
            reason=f"connecteur={connector}, corroboration=téléphone/domaine",
        ))
    return pairs


def find_moe_hors_cible(session: Session) -> List[HorsCibleHit]:
    """Défaut #2 : fiches mon_architecte_interieur dont le NOM est dominé par
    un métier voisin hors cible (maître d'œuvre/constructeur/bureau d'études),
    sans mention archi/décoration. Lecture seule."""
    rows = session.exec(
        select(Opportunity).where(
            col(Opportunity.source_ref).like("monarchitecteinterieur:%")
        )
    ).all()
    out: List[HorsCibleHit] = []
    for o in rows:
        if _is_hors_cible_maitre_oeuvre(o.establishment_name or "", ""):
            out.append(HorsCibleHit(
                id=o.id, source_ref=o.source_ref or "", name=o.establishment_name,
                reason="maître d'œuvre/constructeur/bureau d'études "
                       "(aucune mention architecture/décoration)",
            ))
    return out


def find_monaco_hors_cible(session: Session) -> List[HorsCibleHit]:
    """Défaut #3 : fiches monacomania SANS le tag « mention architecture
    d'intérieur » posé à l'ingestion d'origine (preuve stockée de la garde
    intérieur — cf. `annuaires.monacomania.to_candidates`). Lecture seule."""
    rows = session.exec(
        select(Opportunity).where(col(Opportunity.source_ref).like("monacomania:%"))
    ).all()
    out: List[HorsCibleHit] = []
    for o in rows:
        if MONACOMANIA_INTERIOR_TAG not in (o.secondary_signals or []):
            out.append(HorsCibleHit(
                id=o.id, source_ref=o.source_ref or "", name=o.establishment_name,
                reason="cabinet bâtiment pur "
                       "(aucune mention intérieur/décoration/aménagement à l'ingestion)",
            ))
    return out


def _delete_opportunity(session: Session, opp: Opportunity) -> None:
    session.exec(delete(ContactHistory).where(ContactHistory.opportunity_id == opp.id))
    session.exec(delete(Signal).where(Signal.opportunity_id == opp.id))
    session.delete(opp)


def apply_duplicate_merges(session: Session, pairs: List[DuplicatePair]) -> None:
    """Fusionne chaque paire : comble les champs vides du survivant (jamais
    d'écrasement, VIDE > FAUX), supprime le doublon. Écrit en base."""
    for p in pairs:
        keep = session.get(Opportunity, p.keep_id)
        drop = session.get(Opportunity, p.drop_id)
        if keep is None or drop is None:
            continue  # déjà traité / disparu entre-temps -- idempotent
        keep.phone = keep.phone or drop.phone
        keep.website = keep.website or drop.website
        keep.address = keep.address or drop.address
        keep.email = keep.email or drop.email
        session.add(keep)
        _delete_opportunity(session, drop)
    session.commit()


def apply_purge(session: Session, hits: List[HorsCibleHit]) -> None:
    """Supprime chaque fiche hors-cible (avec Signal + ContactHistory). Écrit
    en base."""
    for h in hits:
        opp = session.get(Opportunity, h.id)
        if opp is not None:
            _delete_opportunity(session, opp)
    session.commit()


def _print_report(dupes: List[DuplicatePair], moe: List[HorsCibleHit],
                   monaco: List[HorsCibleHit], applied: bool) -> None:
    print("=" * 70)
    print("CLEANUP ANNUAIRES 2026-07-18 —", "APPLIQUÉ" if applied else "LISTE (dry-run)")
    print("=" * 70)
    print(f"\n[1] Doublons intra-annuaire : {len(dupes)}")
    for p in dupes:
        print(f"  garde #{p.keep_id} ({p.keep_ref}) <- fusionne #{p.drop_id} "
              f"({p.drop_ref}) -- {p.name!r} / {p.city!r} [{p.reason}]")
    print(f"\n[2] Hors-cible maître d'œuvre (mon_architecte_interieur) : {len(moe)}")
    for h in moe:
        print(f"  purge #{h.id} ({h.source_ref}) -- {h.name!r} [{h.reason}]")
    print(f"\n[3] Hors-cible intérieur (monacomania) : {len(monaco)}")
    for h in monaco:
        print(f"  purge #{h.id} ({h.source_ref}) -- {h.name!r} [{h.reason}]")
    print("=" * 70)
    if not applied:
        print("Mode LISTE (dry-run) -- rien n'a été modifié. Relancer avec "
              "--apply pour fusionner/purger.")
    else:
        print(f"Appliqué : {len(dupes)} fusion(s), {len(moe) + len(monaco)} purge(s).")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Nettoie les 3 défauts qualité annuaires (2026-07-18)."
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Applique les fusions/purges (par défaut : liste seule, lecture).",
    )
    args = parser.parse_args()

    from app.database import DATABASE_URL, engine

    if args.apply and DATABASE_URL.startswith("sqlite:///"):
        import shutil
        from datetime import datetime
        from pathlib import Path

        db_path = Path(DATABASE_URL[len("sqlite:///"):])
        if db_path.exists():
            bak = db_path.with_name(
                f"{db_path.name}.bak-cleanupannuaires-{datetime.now():%Y%m%d-%H%M%S}")
            shutil.copy2(db_path, bak)
            print(f"Sauvegarde : {bak}")

    with Session(engine) as session:
        dupes = find_intra_annuaire_duplicates(session)
        moe = find_moe_hors_cible(session)
        monaco = find_monaco_hors_cible(session)

        if args.apply:
            apply_duplicate_merges(session, dupes)
            apply_purge(session, moe + monaco)

        _print_report(dupes, moe, monaco, applied=args.apply)


if __name__ == "__main__":
    main()
