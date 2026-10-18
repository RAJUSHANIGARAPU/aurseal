"""Finance examples: a billing client on the demo, and isolated verdict files.

The detectors here drive both readings on purpose (``reproduces`` with a fault on,
``cleared`` with it off), so their verdicts go to a per-test file instead of the run's
evidence, where a deliberate ``cleared`` would read as a defect that needs closing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from api.clients.ledger_client import LedgerClient


@pytest.fixture(autouse=True)
def isolated_verdicts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "verdicts" / "verdicts.jsonl"
    monkeypatch.setenv("VERDICTS_FILE", str(path))
    monkeypatch.setenv("VERDICT_STATUS_FILE", str(tmp_path / "verdicts" / "status.md"))
    for name in ("KNOWN_DEFECTS_CLOSED", "COVERAGE_VERDICT_REQUIRED"):
        monkeypatch.delenv(name, raising=False)
    return path


@pytest.fixture
def billing(demo_http) -> LedgerClient:
    """The project's billing client, pointed at the demo's ``/api/``."""
    return LedgerClient(demo_http)
