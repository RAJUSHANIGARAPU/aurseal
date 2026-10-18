"""Pytest fixtures for the test-data lifecycle.

* ``cleanup_ledger`` — a per-test :class:`test_data.cleanup.CleanupLedger`;
  everything recorded is discarded at teardown, pass or fail. Items that could
  not be discarded are logged and added to the test's ``user_properties`` as
  ``cleanup_failed`` so a report can list them; they do not fail the test,
  which would hide its real outcome.
* ``seed_config`` — the run's :class:`test_data.seed_mode.SeedConfig`. A
  contradictory configuration is a usage error at session start, not a
  failure in whichever test happens to ask first.

Per-test and per-session state only, so it is safe under xdist without any
controller/worker coordination.

Register with ``-p test_data.pytest_plugin`` or ``pytest_plugins`` in a conftest.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from test_data.cleanup import CleanupLedger
from test_data.seed_mode import SeedConfig, SeedConfigError, resolve


@pytest.fixture
def cleanup_ledger(request: pytest.FixtureRequest) -> Iterator[CleanupLedger]:
    ledger = CleanupLedger()
    yield ledger
    report = ledger.discard_all()
    for label in report.failed:
        request.node.user_properties.append(("cleanup_failed", label))


@pytest.fixture(scope="session")
def seed_config() -> SeedConfig:
    try:
        return resolve()
    except SeedConfigError as exc:
        raise pytest.UsageError(str(exc)) from exc
