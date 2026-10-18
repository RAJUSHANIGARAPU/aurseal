"""Isolation for the results-store tests: a private database, run id and side files per test."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from results import migrations

ROOT = Path(__file__).resolve().parents[2]

_ENV = (
    "RESULTS_DB",
    "RESULTS_DIR",
    "RESULTS_STORE",
    "RESULTS_STORE_REQUIRED",
    "RESULTS_LANE",
    "RESULTS_OPEN_LOG",
    "RESULTS_ARTIFACT_ROOT",
    "RUN_TRUST",
    "RUN_TRUST_DIR",
    "RUN_TRUST_LANE",
    "VERDICTS_FILE",
    "DATA_ORIGIN_FILE",
    "GITHUB_SHA",
    "GITHUB_REF_NAME",
    "GITHUB_HEAD_REF",
    "GITHUB_ACTIONS",
    "GITHUB_RUN_ID",
    "GITHUB_EVENT_NAME",
    "BUILD_URL",
    "RELEASE_LABEL",
)

# The rewrite warning appears when the outer session has already imported the plugin.
PLUGIN = (
    "-p",
    "results.pytest_plugin",
    "-p",
    "no:cacheprovider",
    "-W",
    "ignore::pytest.PytestAssertRewriteWarning",
)


def pytest_configure(config):
    # The results plugin registers these when it is wired in; the tests here use them now.
    from results.catalogue import register_markers

    register_markers(config)


@pytest.fixture(autouse=True)
def isolated_results(monkeypatch, tmp_path):
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TEST_RUN_ID", f"test-{uuid.uuid4().hex[:12]}")
    monkeypatch.setenv("RESULTS_DIR", str(tmp_path / "results-dir"))
    monkeypatch.setenv("VERDICTS_FILE", str(tmp_path / "verdicts.jsonl"))
    monkeypatch.setenv("DATA_ORIGIN_FILE", str(tmp_path / "data-origin.jsonl"))
    yield
    migrations._extra_domains.clear()


@pytest.fixture
def db_path(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "results.db"
    monkeypatch.setenv("RESULTS_DB", str(path))
    return path


@pytest.fixture
def store(db_path):
    from results.store import open_store

    s = open_store(str(db_path))
    yield s
    s.close()


@pytest.fixture
def subprocess_env(monkeypatch):
    """Lets a pytester subprocess import the framework packages."""
    monkeypatch.setenv("PYTHONPATH", str(ROOT))
