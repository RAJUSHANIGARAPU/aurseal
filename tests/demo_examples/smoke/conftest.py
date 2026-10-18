"""Fixtures for the smoke examples: the demo service and a read-only view of it."""

from __future__ import annotations

from pathlib import Path

import pytest
import requests

from demo_sut.pytest_plugin import (  # noqa: F401 - fixtures are used by name
    demo_control,
    demo_http,
    demo_seed,
    demo_sut,
)
from gate.read_only import ReadOnlyClient


@pytest.fixture
def read_only(demo_sut):  # noqa: F811
    """GET/HEAD only, on the demo's base URL."""
    session = requests.Session()
    yield ReadOnlyClient.over(session, demo_sut.base_url)
    session.close()


@pytest.fixture(autouse=True)
def isolated_verdicts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Detector examples here switch their own faults on: keep their verdicts out of the run's
    evidence file, one file per test."""
    path = tmp_path / "verdicts" / "verdicts.jsonl"
    monkeypatch.setenv("VERDICTS_FILE", str(path))
    monkeypatch.delenv("KNOWN_DEFECTS_CLOSED", raising=False)
    return path
