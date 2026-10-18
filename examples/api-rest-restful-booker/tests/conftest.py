"""Fixtures every test can use: settings, logging and a per-test artefact directory."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from api_rest_restful_booker.log import configure_logging
from api_rest_restful_booker.settings import Settings, get_settings

ARTIFACTS = Path("reports") / "artifacts"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """A rerun hides a problem unless someone wrote down which one: require a reason."""
    for item in items:
        for mark in item.iter_markers("flaky"):
            if not str(mark.kwargs.get("reason", "")).strip():
                raise pytest.UsageError(f"{item.nodeid}: @pytest.mark.flaky needs reason='...'")


@pytest.fixture(scope="session")
def settings() -> Settings:
    current = get_settings()
    configure_logging(current.log_level)
    return current


@pytest.fixture
def artifact_dir(request: pytest.FixtureRequest) -> Path:
    """A directory for this test's own evidence (request logs, downloads, dumps)."""
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", request.node.nodeid)[-120:]
    path = ARTIFACTS / safe
    path.mkdir(parents=True, exist_ok=True)
    return path
