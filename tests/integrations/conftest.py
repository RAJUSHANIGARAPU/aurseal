"""Offline isolation for the adapter tests: no token, no repository from the environment."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def no_github_env(monkeypatch, tmp_path):
    for name in (
        "GITHUB_TOKEN",
        "DEFECT_TRACKER_TOKEN",
        "GITHUB_REPOSITORY",
        "GITHUB_API_URL",
        "GITHUB_STEP_SUMMARY",
        "GITHUB_EVENT_PATH",
        "DEFECT_STATUS_FILE",
        "KNOWN_DEFECTS_CLOSED",
        "REQUIREMENT_ID_PREFIXES",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TRACEABILITY_DIR", str(tmp_path / "trace-out"))
