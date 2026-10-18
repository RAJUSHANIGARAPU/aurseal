"""Isolation for the run-gate tests: each test gets its own run id and a fresh breaker."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

import gate.circuit_breaker as circuit_breaker

ROOT = Path(__file__).resolve().parents[2]

_GATE_ENV = (
    "RUN_TRUST",
    "RUN_TRUST_MIN_EXECUTED",
    "RUN_TRUST_MAX_SKIP_RATIO",
    "RUN_TRUST_MIN_VERDICTS",
    "RUN_TRUST_MIN_ACQUIRED",
    "RUN_TRUST_DIR",
    "RUN_TRUST_LANE",
    "RUN_TRUST_DETECTOR_MARKERS",
    "RUN_TRUST_SEEDED_MARKERS",
    "VERDICTS_FILE",
    "DATA_ORIGIN_FILE",
    "CIRCUIT_BREAKER",
    "CIRCUIT_BREAKER_HOSTS",
    "CIRCUIT_BREAKER_UPSTREAM_LIMIT",
    "FAILFAST_JOURNEY_GROUP",
    "JOURNEY_GROUP_TOLERATE_TIMEOUTS",
    "FAILFAST_PAGE_PATHS",
    "SUITE_REQUIRED",
    "VERDICT_REQUIRED",
    "KNOWN_DEFECTS_CLOSED",
    "CI",
    "GITHUB_ACTIONS",
)


@pytest.fixture(autouse=True)
def isolated_gate(monkeypatch):
    for name in _GATE_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TEST_RUN_ID", f"test-{uuid.uuid4().hex[:12]}")
    monkeypatch.setattr(
        circuit_breaker, "_default", circuit_breaker.ServerErrorBreaker(enabled=False)
    )
    yield


@pytest.fixture
def subprocess_env(monkeypatch):
    """Lets a pytester subprocess import the framework packages."""
    monkeypatch.setenv("PYTHONPATH", str(ROOT))
