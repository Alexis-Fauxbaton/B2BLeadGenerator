"""Service de scoring d'opportunité (0 à 10).

Le score est calculé à partir du signal principal, des signaux secondaires,
de la fraîcheur du signal et des informations disponibles. Il renvoie aussi
une explication lisible par un humain.
"""
from dataclasses import dataclass
from datetime import date
from typing import List, Optional

OPENING_SIGNALS = {"ouverture prochaine", "création récente", "nouveau point de vente"}
TAKEOVER_SIGNALS = {"reprise", "changement propriétaire"}
RENOVATION_SIGNALS = {"rénovation", "travaux visibles"}
RECRUITMENT_SIGNALS = {"recrutement"}
# Signaux NEUTRES d'inventaire (leads « en base » du funnel Insta : établis,
# chaînes multi-sites). Ils ne dénotent AUCUN moment d'achat -> aucun bonus de
# nature. Réunis dans une SEULE famille pour que le couple d'un lead
# chain_multisite (« établissement en activité » + « extension multi-sites »)
# ne déclenche PAS le bonus « signaux croisés » (2 familles) — un même état
# (l'établissement existe et a plusieurs sites) décrit par deux libellés.
INVENTORY_SIGNALS = {"établissement en activité", "extension multi-sites"}

# Famille de chaque libellé de signal. Le bonus "signaux croisés" compte les
# FAMILLES distinctes, pas les libellés : "reprise" + "changement propriétaire"
# décrivent un même événement (une reprise) -> une seule famille, pas de bonus.
SIGNAL_FAMILY = {
    **{s: "opening" for s in OPENING_SIGNALS},
    **{s: "takeover" for s in TAKEOVER_SIGNALS},
    **{s: "renovation" for s in RENOVATION_SIGNALS},
    **{s: "recruitment" for s in RECRUITMENT_SIGNALS},
    **{s: "inventaire" for s in INVENTORY_SIGNALS},
}


def _signal_families(signals) -> set:
    """Familles distinctes parmi des libellés de signaux. Un libellé inconnu
    compte pour lui-même (il reste "croisé" s'il diffère vraiment des autres)."""
    return {SIGNAL_FAMILY.get(s, s) for s in signals}


@dataclass
class ScoreResult:
    score: int
    reason: str


def compute_score(
    main_signal: str,
    secondary_signals: Optional[List[str]],
    detection_date: date,
    probable_needs: Optional[List[str]],
    decision_maker: Optional[str],
    recommended_channel: Optional[str],
    today: Optional[date] = None,
    segment: Optional[str] = None,
    review_count: Optional[int] = None,
) -> ScoreResult:
    secondary_signals = secondary_signals or []
    probable_needs = probable_needs or []
    today = today or date.today()

    all_signals = {main_signal, *secondary_signals}
    points = 0
    reasons: List[str] = []

    age_days = (today - detection_date).days

    # Fraîcheur du signal (gradient : la spec borne <30j et >120j,
    # on remplit l'intervalle pour obtenir un vrai étalement des scores).
    if age_days <= 15:
        points += 2
        reasons.append("signal très récent (< 15 jours)")
    elif age_days <= 30:
        points += 1
        reasons.append("signal récent (< 30 jours)")
    elif age_days <= 90:
        pass
    elif age_days <= 120:
        points -= 1
        reasons.append("signal qui date (> 90 jours)")
    else:
        points -= 2
        reasons.append("signal ancien (> 120 jours)")

    # Nature des signaux (moments d'achat les plus forts)
    if all_signals & OPENING_SIGNALS:
        points += 3
        reasons.append("ouverture prochaine")
    if all_signals & TAKEOVER_SIGNALS:
        points += 3
        reasons.append("reprise / changement de propriétaire")
    if all_signals & RENOVATION_SIGNALS:
        points += 2
        reasons.append("rénovation / travaux")
    if all_signals & RECRUITMENT_SIGNALS:
        points += 2
        reasons.append("recrutement actif")

    # Signaux croisés (gradient : 2 familles = +1, 3+ = +2). On compte les
    # FAMILLES distinctes pour ne pas récompenser un même événement décrit par
    # deux libellés (ex. reprise + changement propriétaire = 1 seule famille).
    distinct = len(_signal_families(all_signals))
    if distinct >= 3:
        points += 2
        reasons.append("plusieurs signaux croisés")
    elif distinct == 2:
        points += 1
        reasons.append("signaux croisés")

    # Données de qualification (bonus seulement s'ils sont réellement exploitables)
    if decision_maker and ("," in decision_maker):
        # Un décideur nommé (ex: "Sarah Oria, gérante") vaut mieux qu'un rôle générique.
        points += 1
        reasons.append("décideur nommé identifié")
    if recommended_channel and recommended_channel != "telephone":
        # Le téléphone est le canal par défaut : il ne constitue pas un signal en soi.
        points += 1
        reasons.append("canal de contact clair")
    if probable_needs and len(probable_needs) >= 2:
        points += 1
        reasons.append("besoin probable clair")

    # Pertinence prospect : un service/à domicile (traiteur) n'a pas de salle à
    # aménager -> moins pertinent pour le fournisseur, on déprioritise.
    if segment == "service":
        points -= 2
        reasons.append("profil services/à domicile (moins pertinent pour l'aménagement de lieu)")

    # Fraîcheur réelle du marché (nb d'avis Places) : raffine la fraîcheur
    # "paperasse" (date BODACC). Peu d'avis = fenêtre d'aménagement encore
    # ouverte ; beaucoup d'avis = établissement déjà installé (achat passé).
    if review_count is not None:
        if review_count <= 20:
            points += 1
            reasons.append("très peu d'avis (établissement tout récent, fenêtre d'aménagement ouverte)")
        elif review_count >= 200:
            points -= 1
            reasons.append("nombreux avis (établissement déjà installé)")

    score = max(0, min(10, points))

    if score >= 8:
        prefix = "Score élevé"
    elif score >= 5:
        prefix = "Score moyen"
    else:
        prefix = "Score faible"

    if reasons:
        reason = f"{prefix} car l'établissement combine : {', '.join(reasons)}."
    else:
        reason = f"{prefix} : peu de signaux exploitables pour le moment."

    return ScoreResult(score=score, reason=reason)
