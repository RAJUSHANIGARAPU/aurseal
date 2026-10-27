"""
Opt-in fixtures: log in once per run, tag the browser's traffic, collect evidence.

Nothing imports this module by default. To use it, import the fixtures **by name**
into a ``conftest.py`` (see the integration notes for the exact lines):

* ``auth_storage_state`` (session) — runs ``core.auth.ui_login.login_once`` with
  ``UniversalLoginPage`` when ``AUTH_MODE=ui`` and returns the storage-state path
  every context should start from; ``None`` for any other mode;
* ``browser_evidence`` (function) — a ``BrowserEvidence`` attached to the test's
  context, so console errors, page errors and failed requests of every page land
  in the failure bundle.

The functions behind the fixtures (``storage_state_for`` and
``tagged_context_args``) are plain functions so they can be tested without a
browser.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core import run_id
from core.auth.session_token_store import SessionTokenStore
from core.auth.settings import AuthMode, AuthSettings
from core.auth.ui_login import login_once
from core.browser_evidence import BrowserEvidence
from core.logger import logger


def storage_state_for(
    browser, settings: AuthSettings, cache: SessionTokenStore, login_page_factory
) -> Path | None:
    """The storage state to start contexts from, logging in first when needed."""
    if settings.mode is not AuthMode.UI:
        return None
    missing = settings.missing_for_mode()
    if missing:
        pytest.skip(
            f"AUTH_MODE=ui needs {', '.join(missing)} (set them in .env.<env> or the environment)"
        )

    from ui.components.universal_login import refuse_if_rejected

    refuse_if_rejected(settings.username)  # before a browser context is opened

    def login(page) -> None:
        login_page_factory(page).login(
            settings.login_url,
            settings.username,
            settings.password,
            app_url=settings.post_login_url,
            organization=settings.organization,
        )

    result = login_once(browser, cache, login)
    logger.info(
        "[auth] storage state %s (%s)", result.storage_state, "reused" if result.reused else "fresh"
    )
    return result.storage_state


def tagged_context_args(base_args: dict, browser, storage_state: Path | None = None) -> dict:
    """``browser_context_args`` plus the run-tagged User-Agent and the saved login."""
    args = dict(base_args)
    if "user_agent" in args:
        tagged = run_id.browser_user_agent(args["user_agent"])
    else:
        tagged = run_id.tagged_user_agent(browser)
    if tagged:
        args["user_agent"] = tagged
    if storage_state is not None:
        args["storage_state"] = str(storage_state)
    return args


@pytest.fixture(scope="session")
def auth_storage_state(browser, config):
    from ui.components.universal_login import UniversalLoginPage

    settings = AuthSettings.from_config(config)
    cache = SessionTokenStore(settings.cache_key(), session_ttl_s=settings.session_ttl_s)
    return storage_state_for(browser, settings, cache, UniversalLoginPage)


@pytest.fixture
def browser_evidence(context):
    evidence = BrowserEvidence().attach_context(context)
    yield evidence
