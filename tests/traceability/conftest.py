"""Isolation for the traceability tests: private output dirs, run id and settings per test."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

_ENV = (
    "TRACEABILITY_DIR",
    "REQUIREMENTS_FILE",
    "REQUIREMENTS_STRICT",
    "REQUIREMENT_ID_PATTERN",
    "REQUIREMENT_ID_PREFIXES",
    "REQUIREMENT_URL_PATTERN",
    "RUN_TRUST",
    "RUN_TRUST_LANE",
    "RUN_TRUST_DIR",
    "RESULTS_DB",
    "RESULTS_DIR",
    "KNOWN_DEFECTS_CLOSED",
    "GITHUB_TOKEN",
    "GITHUB_STEP_SUMMARY",
    "GITHUB_EVENT_PATH",
    "GITHUB_REPOSITORY",
)

# Loads the plugin in a pytester subprocess; the warning appears when the outer session
# already imported it.
PLUGIN = (
    "-p",
    "traceability.pytest_plugin",
    "-p",
    "no:cacheprovider",
    "-W",
    "ignore::pytest.PytestAssertRewriteWarning",
)

REGISTRY = """\
requirements:
  - id: REQ-1
    title: first
    priority: must
  - id: REQ-2
    title: second
    priority: should
  - id: REQ-3
    title: third
    priority: must
  - id: REQ-9
    title: gone
    status: retired
"""


@pytest.fixture(autouse=True)
def isolated_traceability(monkeypatch, tmp_path):
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TEST_RUN_ID", f"trace-{uuid.uuid4().hex[:12]}")
    monkeypatch.setenv("TRACEABILITY_DIR", str(tmp_path / "trace-out"))
    monkeypatch.setenv("VERDICTS_FILE", str(tmp_path / "verdicts.jsonl"))
    monkeypatch.setenv("RESULTS_STORE", "off")
    yield


@pytest.fixture
def subprocess_env(monkeypatch):
    """Lets a pytester subprocess import the framework packages."""
    monkeypatch.setenv("PYTHONPATH", str(ROOT))


@pytest.fixture
def registry_file(tmp_path) -> Path:
    path = tmp_path / "requirements" / "requirements.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(REGISTRY, encoding="utf-8")
    return path
