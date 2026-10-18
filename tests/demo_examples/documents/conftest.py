"""Document examples: a document-service client on the demo, and isolated verdict files.

The detectors here drive both readings on purpose (``reproduces`` with the fault on,
``cleared`` with it off), so their verdicts go to a per-test file instead of the run's
evidence, where a deliberate ``cleared`` would read as a defect that needs closing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from api.clients.document_service import DocumentServiceClient


@pytest.fixture(autouse=True)
def isolated_verdicts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "verdicts" / "verdicts.jsonl"
    monkeypatch.setenv("VERDICTS_FILE", str(path))
    monkeypatch.setenv("VERDICT_STATUS_FILE", str(tmp_path / "verdicts" / "status.md"))
    for name in ("KNOWN_DEFECTS_CLOSED", "COVERAGE_VERDICT_REQUIRED"):
        monkeypatch.delenv(name, raising=False)
    return path


@pytest.fixture
def documents(demo_http) -> DocumentServiceClient:
    """The project's document-service client, pointed at the demo's ``/api/``."""
    return DocumentServiceClient(demo_http)
