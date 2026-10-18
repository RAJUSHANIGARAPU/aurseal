"""Fixtures ``db_probe`` and ``db_state`` (load with ``-p checks.db.pytest_plugin``).

The database comes from ``--db-url`` or ``DB_URL``. Without one, a test using the
fixtures is skipped with a named reason — it never passes having checked nothing.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from checks.db.probe import open_probe
from checks.db.state import DbState

URL_ENV = "DB_URL"
METRICS_ENV = "DB_SNAPSHOT_METRICS"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.getgroup("db").addoption(
        "--db-url", default=None, help=f"database to probe read-only (env {URL_ENV})"
    )


@pytest.fixture(scope="session")
def db_url(request: pytest.FixtureRequest) -> str:
    url = request.config.getoption("--db-url", default=None) or os.environ.get(URL_ENV)
    if not url:
        pytest.skip(f"no database to check: set {URL_ENV} or --db-url")
    return url


@pytest.fixture(scope="session")
def db_probe(db_url: str) -> Iterator:
    probe = open_probe(db_url)
    try:
        yield probe
    finally:
        probe.close()


@pytest.fixture
def db_state(db_probe) -> DbState:
    record = (os.environ.get(METRICS_ENV) or "").lower() in ("1", "true", "yes", "on")
    return DbState(db_probe, record=record)
