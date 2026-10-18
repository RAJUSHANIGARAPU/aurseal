"""
Acquiring a token by logging in through the real login page, once.

``login_once`` opens a throwaway context, runs a login routine (normally
``ui.components.universal_login.UniversalLoginPage.login``), and keeps two things:

* the **bearer token**, captured from the traffic rather than scraped from the
  page — the ``Authorization`` header of the app's own API calls, or the
  ``access_token`` of a token-endpoint response. ``storage`` scanning for a JWT is
  the fallback, for apps that keep the token in ``localStorage`` and have not
  called anything yet;
* the **storage state**, saved atomically to the cache so every later context in
  the run starts logged in (``browser.new_context(storage_state=path)``).

A still-valid cache short-circuits the whole thing: no browser, no login.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from core.auth.session_token_store import SessionTokenStore, looks_like_jwt
from core.logger import logger

TOKEN_ENDPOINT_PATTERN = re.compile(r"/(oauth2?/)?token(\?|$)")
_BEARER = re.compile(r"^\s*Bearer\s+(\S+)\s*$", re.IGNORECASE)

_STORAGE_SCAN = """() => {
  const found = [];
  for (const store of [window.localStorage, window.sessionStorage]) {
    for (let i = 0; i < store.length; i++) { found.push(store.getItem(store.key(i)) || ''); }
  }
  return found;
}"""


def bearer_from_headers(headers: Mapping[str, str]) -> str | None:
    """The JWT in an ``Authorization: Bearer`` header, or None."""
    for name, value in headers.items():
        if name.lower() == "authorization":
            match = _BEARER.match(value or "")
            if match and looks_like_jwt(match.group(1)):
                return match.group(1)
    return None


def token_from_body(body: object) -> str | None:
    """``access_token`` (or ``id_token`` as a last resort) from a token-endpoint JSON body."""
    if isinstance(body, (str, bytes)):
        try:
            body = json.loads(body)
        except ValueError:
            return None
    if not isinstance(body, dict):
        return None
    for key in ("access_token", "id_token"):
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def token_from_storage_values(values: list[str]) -> str | None:
    """The first JWT among raw storage values, including JWTs nested in JSON blobs."""
    for value in values:
        if looks_like_jwt(value):
            return value
        for candidate in re.findall(
            r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*", value or ""
        ):
            if looks_like_jwt(candidate):
                return candidate
    return None


class BearerTokenSniffer:
    """Listens on a page for the first bearer token the app sends or receives."""

    def __init__(self, token_endpoint: re.Pattern[str] = TOKEN_ENDPOINT_PATTERN) -> None:
        self.token: str | None = None
        self.source: str | None = None
        self._endpoint = token_endpoint

    def attach(self, page) -> BearerTokenSniffer:
        page.on("request", self._on_request)
        page.on("response", self._on_response)
        return self

    def _on_request(self, request) -> None:
        if self.token:
            return
        token = bearer_from_headers(request.headers)
        if token:
            self.token, self.source = token, "request header"

    def _on_response(self, response) -> None:
        if self.token or not self._endpoint.search(response.url):
            return
        try:
            token = token_from_body(response.text())
        except Exception:  # noqa: BLE001 - bodies of redirects are unreadable
            return
        if token:
            self.token, self.source = token, "token endpoint"


@dataclass(frozen=True)
class LoginResult:
    token: str | None  # None for a cookie-only session
    storage_state: Path
    reused: bool


def login_once(
    browser,
    cache: SessionTokenStore,
    login: Callable[[object], None],
    *,
    context_args: dict | None = None,
) -> LoginResult:
    """
    The cached login while it is still usable; otherwise log in and cache the result.

    ``login`` receives a fresh page and must leave it on the logged-in app. A
    cookie-only login (no token was ever seen) is reused on the storage state's
    age alone; a login that had a token is reused only while that token is.
    """
    had_token = cache.token_path.exists()
    cached = cache.load_token()
    if cache.storage_state_valid() and (cached or not had_token):
        logger.debug("[auth] reusing cached login")
        return LoginResult(cached, cache.storage_state_path, reused=True)
    context = browser.new_context(**(context_args or {}))
    try:
        page = context.new_page()
        sniffer = BearerTokenSniffer().attach(page)
        login(page)
        token = sniffer.token
        if not token:
            token = token_from_storage_values(page.evaluate(_STORAGE_SCAN))
            source = "storage scan" if token else None
        else:
            source = sniffer.source
        state_path = cache.save_storage_state(context.storage_state())
        if token:
            cache.save_token(token)
            logger.info("[auth] logged in; bearer token taken from %s", source)
        else:
            cache.token_path.unlink(missing_ok=True)
            logger.info("[auth] logged in; no bearer token seen, session cookies cached")
        return LoginResult(token, state_path, reused=False)
    finally:
        try:
            context.close()
        except Exception as exc:  # noqa: BLE001 - teardown never raises
            logger.warning("[auth] closing the login context failed: %s", exc)
