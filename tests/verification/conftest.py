"""Keep the verdict machinery's self-tests out of the real run's evidence.

These tests drive every verdict branch on purpose. Each one gets its own
verdict and status files, and a clean slate for the variables that would
otherwise leak the outer run's state in (or this module's out).
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_verdicts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "verdicts" / "verdicts.jsonl"
    monkeypatch.setenv("VERDICTS_FILE", str(path))
    monkeypatch.setenv("VERDICT_STATUS_FILE", str(tmp_path / "verdicts" / "status.md"))
    for name in ("TEST_RUN_ID", "KNOWN_DEFECTS_CLOSED", "COVERAGE_VERDICT_REQUIRED"):
        monkeypatch.delenv(name, raising=False)
    return path
