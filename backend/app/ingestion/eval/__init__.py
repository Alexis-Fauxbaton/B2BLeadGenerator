"""Harness d'évaluation de la classification des leads Instagram.

Mesure la précision du bucket `a_contacter`, le rappel des `opening` et la
matrice de confusion, sur un jeu de vérité terrain annoté à la main
(`instagram_groundtruth.csv`) et des snapshots de profils figés
(`snapshots/<handle>.json`). Objectif : MESURER avant de régler quoi que ce soit
(cf. HANDOFF.md — discipline anti-overfit)."""
