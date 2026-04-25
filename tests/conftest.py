"""Local conftest for tests/ directory.

Overrides the global conftest fixtures that require a live Playwright browser context.
The api_agent tests are pure Python unit tests and do not use a browser.
"""

import pytest


@pytest.fixture(autouse=True)
def collect_browser_evidence():
    """No-op override: api_agent tests do not use a browser."""
    yield


@pytest.fixture(autouse=True)
def enable_artifacts():
    """No-op override: api_agent tests do not use Playwright tracing."""
    yield
