"""Fixtures for the scheduler examples: one demo instance per session (per xdist worker)."""

from __future__ import annotations

from pathlib import Path

import pytest

from demo_sut.pytest_plugin import (  # noqa: F401 - fixtures are used by name
    demo_control,
    demo_http,
    demo_seed,
    demo_sut,
)

NOW = "2026-03-10T09:00:00Z"


@pytest.fixture
def frozen(demo_control):  # noqa: F811
    """The demo clock frozen at 2026-03-10 09:00 UTC for the test."""
    demo_control.freeze(NOW)
    return demo_control


@pytest.fixture(autouse=True)
def isolated_verdicts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Detector examples here switch their own faults on: keep their verdicts out of the run's
    evidence file, one file per test."""
    path = tmp_path / "verdicts" / "verdicts.jsonl"
    monkeypatch.setenv("VERDICTS_FILE", str(path))
    monkeypatch.delenv("KNOWN_DEFECTS_CLOSED", raising=False)
    return path
