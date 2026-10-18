"""Browser fixtures: the base URL and assertion timeout come from settings."""

from __future__ import annotations

import pytest
from playwright.sync_api import expect


@pytest.fixture(scope="session")
def base_url(settings):
    """Overrides the pytest-base-url fixture, so ``page.goto("/")`` goes to the target."""
    return settings.base_url


@pytest.fixture(scope="session", autouse=True)
def _expect_timeout(settings):
    expect.set_options(timeout=settings.expect_timeout_ms)
