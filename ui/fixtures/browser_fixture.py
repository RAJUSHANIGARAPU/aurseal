"""
Custom browser fixture.

Currently not used because pytest-playwright manages the browser lifecycle.
This fixture exists for advanced scenarios such as:

• multi-user tests
• custom browser launch options
• remote browsers
• mobile device emulation

Nothing imports this module and it is not a ``conftest.py``, so none of it runs
today. To use it, import the fixtures you want into the relevant ``conftest.py``
— and import them **by name**. ``browser`` and ``page`` here shadow
pytest-playwright's own fixtures, so pulling in the whole module replaces the
working ones with these.
"""

import os

import pytest
from playwright.sync_api import sync_playwright

from core.config_reader import ConfigReader


@pytest.fixture(scope="session")
def playwright_instance():
    with sync_playwright() as p:
        yield p


def _requested_browser(config) -> str:
    """
    Resolve ``--browser`` to a single engine name.

    The option is declared ``action="append"`` with a default of ``[]``, so it
    is a list. This used to be passed straight to ``getattr``, which raises
    ``TypeError: attribute name must be string`` — meaning the fixture below
    could never have run, template or not.
    """
    requested = config.getoption("--browser") or ["chromium"]
    return requested[0]


@pytest.fixture(scope="function")
def browser(playwright_instance, request):
    headless = request.config.getoption("--headless") == "true"

    browser = getattr(playwright_instance, _requested_browser(request.config)).launch(
        headless=headless
    )

    yield browser
    browser.close()


@pytest.fixture(scope="function")
def page(browser, request):
    os.makedirs("reports/videos", exist_ok=True)
    os.makedirs("reports/traces", exist_ok=True)

    context = browser.new_context(record_video_dir="reports/videos")

    context.tracing.start(screenshots=True, snapshots=True, sources=True)

    page = context.new_page()

    yield page

    context.tracing.stop(path=f"reports/traces/{request.node.name}.zip")

    context.close()


@pytest.fixture(scope="session")
def config(request):
    env = request.config.getoption("--env")
    return ConfigReader(env)
