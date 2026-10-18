"""Keep contract-gate evidence (verdict lines, metric rows) out of the repository's reports/."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("VERDICTS_FILE", str(tmp_path / "verdicts.jsonl"))
    monkeypatch.setenv("METRICS_DIR", str(tmp_path / "metrics"))
