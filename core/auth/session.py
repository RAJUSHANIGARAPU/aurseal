"""
One token per run, refreshed before it expires.

Access tokens are often short-lived (minutes) while a sequential browser run is
long (most of an hour). A token acquired once at session start therefore expires
partway through, and the tests that run after that point fail with 401s that read
like product defects. ``SessionToken.token()`` returns the held token while it is
usable beyond ``refresh_margin_s`` and transparently re-acquires it otherwise.

Order matters for browser tests: resolve the token *before* building the browser
context. A context picks up the cached storage state only if it is still valid, so
renewing the token after the context was built leaves the page logged out.

A token handed in from outside (``AuthMode.TOKEN``) is never refreshed: there is
nothing to refresh it with, and silently swapping it would hide that it expired.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from core.auth.session_token_store import SessionTokenStore
from core.auth.settings import AuthMode
from core.logger import logger

DEFAULT_REFRESH_MARGIN_S = 120


class SessionToken:
    def __init__(
        self,
        acquire: Callable[[], str | None],
        cache: SessionTokenStore,
        mode: AuthMode = AuthMode.UI,
        refresh_margin_s: float = DEFAULT_REFRESH_MARGIN_S,
        supplied_token: str = "",
    ) -> None:
        self._acquire = acquire
        self._cache = cache
        self._mode = mode
        self._margin = refresh_margin_s
        self._supplied_token = supplied_token
        self._token: str | None = None
        self._lock = threading.Lock()
        self.acquisitions = 0

    def token(self) -> str | None:
        """A usable token, acquiring or refreshing one when needed; None when none can be had."""
        if self._mode is AuthMode.NONE:
            return None
        if self._mode is AuthMode.TOKEN:
            return self._supplied_token or None
        with self._lock:
            if self._token and self._cache.is_usable(self._token, self._margin):
                return self._token
            cached = self._cache.load_token(self._margin)
            if cached:
                self._token = cached
                return cached
            if self._token:
                logger.info(
                    "[auth] session token is within %ss of expiry, refreshing", self._margin
                )
            fresh = self._acquire()
            self.acquisitions += 1
            if not fresh:
                logger.warning(
                    "[auth] token acquisition returned nothing; tests will run unauthenticated"
                )
                self._token = None
                return None
            self._cache.save_token(fresh)
            self._token = fresh
            return fresh

    def invalidate(self) -> None:
        with self._lock:
            self._token = None
            self._cache.invalidate()
