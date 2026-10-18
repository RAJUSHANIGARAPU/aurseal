"""Live fixtures: the base URL, the assertion timeout and a scanner set up from settings."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Page, expect

from a11y_w3c_bad.a11y import Scanner
from a11y_w3c_bad.settings import Settings

USER_AGENT = "a11y-suite-example (Playwright; accessibility tests)"


@pytest.fixture(scope="session")
def base_url(settings: Settings) -> str:
    """Overrides the pytest-base-url fixture. The trailing slash lets a relative page path
    (``"checkout/"``) resolve under a target that lives below the site root."""
    return settings.base_url + "/"


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args: dict[str, Any]) -> dict[str, Any]:
    """Say who is calling. The site's CDN answers a headless browser's default user agent with
    a bot challenge (HTTP 403), which a scan would otherwise judge instead of the demo."""
    return {**browser_context_args, "user_agent": USER_AGENT}


@pytest.fixture(scope="session", autouse=True)
def _expect_timeout(settings: Settings) -> None:
    expect.set_options(timeout=settings.expect_timeout_ms)


@pytest.fixture(scope="session")
def axe_script(settings: Settings) -> str | None:
    if not settings.axe_script:
        return None
    return Path(settings.axe_script).read_text(encoding="utf-8")


@pytest.fixture
def scanner(page: Page, settings: Settings, axe_script: str | None) -> Scanner:
    tags = [tag.strip() for tag in settings.wcag_tags.split(",") if tag.strip()]
    return Scanner(page, tags=tags, script=axe_script)
