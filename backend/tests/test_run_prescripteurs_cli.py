"""CLI + endpoint prescripteurs (A1, T6) — sans réseau."""
import app.ingestion.pipeline as pl


def test_cli_has_prescripteurs_mode(monkeypatch):
    import sys
    import app.ingestion.run as run
    from app.ingestion.pipeline import IngestStats
    called = {}

    def fake(**k):
        called["k"] = k
        return IngestStats(source="instagram", mode="prescripteurs")

    monkeypatch.setattr(run, "run_prescripteurs", fake)
    monkeypatch.setattr(sys, "argv", ["run", "--mode", "prescripteurs", "--limit", "12"])
    run.main()
    assert called["k"]["limit"] == 12


def test_run_prescripteurs_exported():
    # run_prescripteurs doit être importable depuis pipeline (contrat CLI/endpoint).
    assert hasattr(pl, "run_prescripteurs")
