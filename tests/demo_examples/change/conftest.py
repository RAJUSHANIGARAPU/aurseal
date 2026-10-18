"""Keep the detector examples' verdicts out of the real run's evidence file.

The detectors here run against invented faults switched on by the test itself, so their
"reproduces" / "cleared" verdicts say nothing about a real system. Each test gets its own
verdict file; the examples read it back to check what was recorded.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_verdicts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "verdicts" / "verdicts.jsonl"
    monkeypatch.setenv("VERDICTS_FILE", str(path))
    monkeypatch.delenv("KNOWN_DEFECTS_CLOSED", raising=False)
    return path
