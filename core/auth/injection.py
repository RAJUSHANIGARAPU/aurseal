"""
Putting a saved login into a browser context that was built without it.

Two token shapes, two mechanisms:

* **A session cookie** (opaque token). The cookies are copied from the cached
  storage state *exactly as the server issued them* — domain, path, ``httpOnly``,
  ``secure``, expiry, ``sameSite``. Rebuilding them by hand is the trap: an
  anti-forgery cookie (``XSRF-TOKEN`` and friends) set on the wrong host is
  invisible to the page's script, so its first write goes out without the echo
  header, is refused, and the app falls back to the login page.
* **A JWT** the single-page app reads from ``localStorage``. Written through
  ``page.evaluate`` with the token passed as an *argument*, never formatted into
  the script source, so a token can never break out of a string literal.

The preferred path is still to build the context with ``storage_state=`` from
the start (see ``SessionTokenStore.storage_state_path``); these helpers exist for the
context that was created before the login finished, or by someone else.
"""

from __future__ import annotations

from collections.abc import Iterable

from core.auth.session_token_store import SessionTokenStore, looks_like_jwt
from core.logger import logger

DEFAULT_STORAGE_KEYS = ("access_token", "token")
_SAME_SITE = {"Strict", "Lax", "None"}


def cookies_from_storage_state(state: dict) -> list[dict]:
    """The cookies of a Playwright storage state, in the shape ``add_cookies`` accepts."""
    cookies = []
    for raw in state.get("cookies", []) or []:
        name, value, domain = raw.get("name"), raw.get("value"), raw.get("domain")
        if not name or value is None or not domain:
            continue
        cookie = {
            "name": name,
            "value": value,
            "domain": domain,
            "path": raw.get("path") or "/",
            "httpOnly": bool(raw.get("httpOnly", False)),
            "secure": bool(raw.get("secure", False)),
        }
        expires = raw.get("expires")
        if isinstance(expires, (int, float)) and expires > 0:
            cookie["expires"] = float(expires)
        if raw.get("sameSite") in _SAME_SITE:
            cookie["sameSite"] = raw["sameSite"]
        cookies.append(cookie)
    return cookies


def restore_session_cookies(context, cache: SessionTokenStore) -> int:
    """Copy the cached login's cookies into ``context``; returns how many (0 = nothing fresh)."""
    state = cache.load_storage_state()
    if not state:
        return 0
    cookies = cookies_from_storage_state(state)
    if cookies:
        context.add_cookies(cookies)
    return len(cookies)


def inject_bearer_into_local_storage(
    page, token: str, keys: Iterable[str] = DEFAULT_STORAGE_KEYS
) -> None:
    """Write ``token`` under each of ``keys`` in the current origin's localStorage."""
    page.evaluate(
        "([token, keys]) => { for (const k of keys) { localStorage.setItem(k, token); } }",
        [token, list(keys)],
    )


def apply_session(context, page, token: str | None, cache: SessionTokenStore) -> str:
    """
    Authenticate a fresh context with whatever the token is. Returns what was done
    (``cookies``, ``local_storage`` or ``none``) so the caller can log or assert it.

    Never raises: an unauthenticated page fails the test that needs auth, with a
    clearer message than a teardown-time exception would give.
    """
    if not token:
        logger.warning("[auth] no token available; the page will not be authenticated")
        return "none"
    try:
        if looks_like_jwt(token):
            inject_bearer_into_local_storage(page, token)
            return "local_storage"
        if restore_session_cookies(context, cache):
            return "cookies"
        logger.warning("[auth] opaque token but no fresh cached session cookies to restore")
        return "none"
    except Exception as exc:  # noqa: BLE001 - see docstring
        logger.warning("[auth] session injection failed: %s; proceeding without auth", exc)
        return "none"
