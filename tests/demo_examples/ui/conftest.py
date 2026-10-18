"""
Browser fixtures for the demo UI examples (marker ``demo_ui``, browser lane only).

``tests/conftest.py`` turns the root browser-evidence fixture into a no-op for
everything under ``tests/``, so this directory attaches its own
``core.browser_evidence.BrowserEvidence`` and writes it, together with the fault
set ``demo_control`` saw, into the test's ``user_properties``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.browser_evidence import BrowserEvidence

DEFAULT_TIMEOUT_MS = 10_000


@pytest.fixture
def demo_page(page, context, demo_sut, demo_control, request):
    """pytest-playwright's ``page``, with evidence collected for the whole context."""
    evidence = BrowserEvidence().attach_context(context)
    page.set_default_timeout(DEFAULT_TIMEOUT_MS)
    yield page
    request.node.user_properties.append(("browser_evidence", json.dumps(evidence.as_dict())))


@pytest.fixture(autouse=True)
def isolated_verdicts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Detector examples here switch their own faults on: keep their verdicts out of the run's
    evidence file, one file per test."""
    path = tmp_path / "verdicts" / "verdicts.jsonl"
    monkeypatch.setenv("VERDICTS_FILE", str(path))
    monkeypatch.delenv("KNOWN_DEFECTS_CLOSED", raising=False)
    return path
