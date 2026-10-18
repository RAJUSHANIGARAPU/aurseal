"""Fixtures every test can use: settings, logging and a per-test artefact directory."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from web_ui_saucedemo.log import configure_logging
from web_ui_saucedemo.settings import Settings, get_settings

ARTIFACTS = Path("reports") / "artifacts"


@pytest.fixture(scope="session", autouse=True)
def _data_test_ids(playwright):
    """Sauce Demo marks its elements with ``data-test``; ``get_by_test_id`` reads that."""
    playwright.selectors.set_test_id_attribute("data-test")


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
