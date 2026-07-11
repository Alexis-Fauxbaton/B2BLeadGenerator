"""Éval 0 faux merge CROSS-SOURCE (B, T6). Généralise le gate A2
annuaire×insta aux sources de masse `sirene_stock`/`places` : le gate est
alimenté par les fusions RÉELLEMENT émises (`stats.soft_merges`) d'un run
`run_places`/`run_stock` offline (api_post/connector factices, DB mémoire).

Invariants :
- `false_merges_cross_source` PURE : ne flagge que les fusions hors vérité ;
- une fusion LÉGITIME (le tél/domaine Places comble une fiche Insta muette)
  n'est PAS flaggée ;
- la fixture ADVERSE inter-masse (homonyme sirene_stock/places même nom+ville
  + même CP mais tél/domaine différents) NE fusionne PAS -> gate vert.
Aucun réseau, aucun LLM."""
from app.ingestion.eval.prescripteurs_metrics import (
    false_merges_annuaire_insta, false_merges_cross_source,
)
from app.ingestion.eval.prescripteurs_run import run_cross_source_gate


def test_false_merges_cross_source_pure():
    truth = {("places:a", "insta_a")}
    assert false_merges_cross_source([("places:a", "insta_a")], truth) == []
    assert false_merges_cross_source([("places:b", "insta_b")], truth) == [
        ("places:b", "insta_b")]
    assert false_merges_cross_source([], truth) == []


def test_annuaire_insta_alias_is_the_generalized_metric():
    # rétro-compat : l'ancien nom pointe vers la métrique généralisée.
    assert false_merges_annuaire_insta is false_merges_cross_source


def test_cross_source_gate_legit_merge_kept_homonym_not_merged():
    res = run_cross_source_gate()
    assert res["gate_zero_false_merge"] is True
    assert res["false_merges"] == []
    # (a) fusion légitime émise : le lead Places comble la fiche Insta muette.
    assert ["places:lumen", "atelier_lumen_insta"] in res["soft_merges"]
    # (b) homonyme inter-masse (Studio Meridien, même CP, contacts différents)
    #     JAMAIS fusionné -> n'apparaît dans aucune paire.
    assert all("meridien" not in a and "meridien" not in b
               for a, b in res["soft_merges"])
