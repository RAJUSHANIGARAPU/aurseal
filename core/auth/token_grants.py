"""
Getting a bearer token without a browser.

Three ways, selected by the auth mode the settings ladder resolved
(``core.auth.settings.resolve_auth_mode``), so a report and the code that
authenticates can never disagree about which one the run used:

* ``password`` — the OAuth 2.0 resource-owner password grant (RFC 6749 §4.3).
  It is **legacy**: OAuth 2.1 removes it and many identity providers disable it
  per client, in which case the endpoint answers ``unauthorized_client`` or
  ``access_denied``. It stays here, opt-in, for systems under test that still
  allow it; the error message carries the provider's own ``error`` code so a
  disabled grant is recognisable at once.
* ``client_credentials`` — machine-to-machine (RFC 6749 §4.4). The client secret
  comes from the ladder or, when blank, from the OS credential store
  (``core.auth.secrets``). ``audience``, ``scope`` and ``organization`` are sent
  only when set.
* ``token`` — a token acquired elsewhere and handed in through ``SUPPLIED_TOKEN``.
  It is checked *before* any request: it must look like a JWT and stay valid
  beyond the margin. Without the check, an expired token turns every test into a
  401 that reads like a product defect. Pass it as an environment variable, not
  on a command line, where CI consoles echo it.

``ui`` mode is not here: it needs a browser and lives in ``core.auth.ui_login``.

Nothing in this module logs or raises with a secret or a token in the text.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from datetime import datetime, timezone

import requests

from api.clients.http_engine import ApiResponse, HttpEngine
from core.auth.secrets import require_secret
from core.auth.session_token_store import DEFAULT_MARGIN_S, jwt_claims, jwt_expiry, looks_like_jwt
from core.auth.settings import AuthMode, AuthSettings
from core.auth.ui_login import token_from_body
from core.logger import logger

SecretLookup = Callable[[str], str]


class TokenGrantError(RuntimeError):
    """A token could not be obtained; the message never contains a secret."""


def _engine(settings: AuthSettings, session: requests.Session | None) -> HttpEngine:
    if not settings.token_url:
        raise TokenGrantError(f"{settings.mode.value} mode needs AUTH_TOKEN_URL")
    return HttpEngine(settings.token_url, session=session, timeout_s=30)


def _optional(settings: AuthSettings) -> dict[str, str]:
    extra = {
        "audience": settings.audience,
        "scope": settings.scope,
        "organization": settings.organization,
    }
    return {key: value for key, value in extra.items() if value}


def _token_from(response: ApiResponse, grant: str) -> str:
    if not response.is_success:
        try:
            body = response.json()
        except ValueError:
            body = {}
        code = body.get("error", "") if isinstance(body, dict) else ""
        description = body.get("error_description", "") if isinstance(body, dict) else ""
        detail = ": ".join(p for p in (code, description) if p) or "no OAuth error in the body"
        raise TokenGrantError(f"{grant} grant refused with HTTP {response.status} ({detail})")
    token = token_from_body(response.content)
    if not token:
        raise TokenGrantError(f"{grant} grant answered {response.status} without an access_token")
    logger.info("[auth] %s grant issued a token", grant)
    return token


def password_grant(settings: AuthSettings, *, session: requests.Session | None = None) -> str:
    """Resource-owner password grant. Legacy; see the module docstring."""
    missing = [
        name
        for name, value in (
            ("AUTH_USERNAME", settings.username),
            ("AUTH_PASSWORD", settings.password),
            ("AUTH_CLIENT_ID", settings.client_id),
        )
        if not value
    ]
    if missing:
        raise TokenGrantError(f"password mode needs {', '.join(missing)}")
    form = {
        "grant_type": "password",
        "username": settings.username,
        "password": settings.password,
        "client_id": settings.client_id,
        **_optional(settings),
    }
    if settings.client_secret:
        form["client_secret"] = settings.client_secret
    response = _engine(settings, session).post(settings.token_url, data=form)
    return _token_from(response, "password")


def client_credentials(
    settings: AuthSettings,
    *,
    session: requests.Session | None = None,
    secret_lookup: SecretLookup = require_secret,
) -> str:
    """Machine-to-machine grant; a blank secret is looked up in the credential store."""
    if not settings.client_id:
        raise TokenGrantError("client_credentials mode needs AUTH_CLIENT_ID")
    secret = settings.client_secret or secret_lookup("AUTH_CLIENT_SECRET")
    form = {
        "grant_type": "client_credentials",
        "client_id": settings.client_id,
        "client_secret": secret,
        **_optional(settings),
    }
    response = _engine(settings, session).post(settings.token_url, data=form)
    return _token_from(response, "client_credentials")


def supplied_token(
    settings: AuthSettings,
    *,
    margin_s: float = DEFAULT_MARGIN_S,
    now: float | None = None,
) -> str:
    """The injected token, refused before any request when it is malformed or expiring."""
    token = settings.supplied_token.strip()
    if not token:
        raise TokenGrantError("token mode needs SUPPLIED_TOKEN")
    if not looks_like_jwt(token) or jwt_claims(token) is None:
        raise TokenGrantError("SUPPLIED_TOKEN is not a readable JWT (header.payload.signature)")
    expiry = jwt_expiry(token)
    if expiry is None:
        if "exp" in (jwt_claims(token) or {}):
            raise TokenGrantError("SUPPLIED_TOKEN has an unreadable exp claim")
        return token
    current = time.time() if now is None else now
    if expiry <= current + margin_s:
        when = datetime.fromtimestamp(expiry, tz=timezone.utc).isoformat(timespec="seconds")
        state = "expired" if expiry <= current else f"expires within {int(margin_s)}s"
        raise TokenGrantError(f"SUPPLIED_TOKEN {state} (exp {when}); acquire a fresh one")
    return token


def resolve_token(
    settings: AuthSettings,
    *,
    session: requests.Session | None = None,
    secret_lookup: SecretLookup = require_secret,
    margin_s: float = DEFAULT_MARGIN_S,
    now: float | None = None,
) -> str | None:
    """
    A token for ``settings.mode``: None for ``none``, a grant for the others.

    Use as the ``acquire`` callable of ``core.auth.session.SessionToken`` to get
    caching and refresh-before-expiry for the two grant modes.
    """
    mode = settings.mode
    if mode is AuthMode.NONE:
        return None
    if mode is AuthMode.TOKEN:
        return supplied_token(settings, margin_s=margin_s, now=now)
    if mode is AuthMode.PASSWORD:
        return password_grant(settings, session=session)
    if mode is AuthMode.CLIENT_CREDENTIALS:
        return client_credentials(settings, session=session, secret_lookup=secret_lookup)
    raise TokenGrantError(
        "ui mode logs in through a browser; use core.auth.ui_login.login_once instead"
    )


def is_authorised_for(
    token: str,
    url: str,
    *,
    session: requests.Session | None = None,
    headers: Mapping[str, str] | None = None,
) -> bool:
    """
    True when ``url`` accepts ``token`` (any answer other than 401/403).

    A cheap probe before a suite that depends on one service: "the token was
    minted" and "the token opens this service" are different claims.
    """
    response = HttpEngine(url, token=token, session=session, headers=headers).get(url)
    return response.status not in (401, 403)
