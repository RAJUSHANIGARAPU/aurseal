"""
Marking each outgoing suite request as test traffic under a single per-run id.

When a shared environment misbehaves, the first question is whether the traffic
that triggered it was ours. The User-Agent is the carrier because it is what
gateways and application logs already record:

* API clients send ``User-Agent: <product>/<run id> python-requests/<ver>`` plus an
  ``X-Test-Run-Id`` header (``api_headers``).
* For the browser, the token is only *added to the end of* its User-Agent
  (``browser_user_agent``). Never an extra header: a page calling an API on another
  origin would turn every call into a CORS preflight, and a WAF that keys on the
  browser's User-Agent must still see what it saw before.

The id resolves as ``TEST_RUN_ID``, then a CI build id (Jenkins ``BUILD_TAG``,
GitHub Actions ``GITHUB_RUN_ID``-``GITHUB_RUN_ATTEMPT``), then
``local-<host>-<yyyymmdd-HHMMSS>``, sanitised to ``[A-Za-z0-9._-]`` and capped at
64 characters. ``TEST_RUN_TAGGING=false`` switches it all off.

Under xdist every worker is its own process and would mint its own local id a
second apart. ``pin_for_session`` exports the controller's id as ``TEST_RUN_ID``
before the workers start, so a run is one id however many workers it has.

A sharded run is one id over several processes, so names a test mints for the data
it seeds also carry the worker: ``TEST_WORKER_PREFIX`` (``w2`` for shard 2, ``ws``
for the serial lane, exported by ``gate.pytest_shard``) goes in front of the tag
(``data_tag``). Two workers then never mint the same name, and a leftover record says
which worker made it. The prefix is ``[a-z0-9]{0,8}``; anything else fails rather
than being dropped, since a silently unprefixed name is the collision it prevents.
"""

from __future__ import annotations

import os
import re
import socket
from collections.abc import Mapping
from datetime import datetime

from core.logger import logger

HEADER = "X-Test-Run-Id"
ID_VARIABLE = "TEST_RUN_ID"
ENABLED_VARIABLE = "TEST_RUN_TAGGING"
PRODUCT_VARIABLE = "TEST_RUN_PRODUCT"
WORKER_PREFIX_VARIABLE = "TEST_WORKER_PREFIX"
DEFAULT_PRODUCT = "test-automation"
MAX_LENGTH = 64

_announced = False


def sanitize(raw: str | None) -> str:
    """Keep ``[A-Za-z0-9._-]``, turn anything else into ``-``, cap at 64 characters."""
    if not raw:
        return ""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", raw.strip()).strip("-")
    return cleaned[:MAX_LENGTH]


def _ci_id(environ: Mapping[str, str]) -> str:
    if environ.get("BUILD_TAG"):
        return environ["BUILD_TAG"]
    if environ.get("GITHUB_RUN_ID"):
        attempt = environ.get("GITHUB_RUN_ATTEMPT", "1")
        return f"gh-{environ['GITHUB_RUN_ID']}-{attempt}"
    return ""


def resolve(environ: Mapping[str, str], host: str, now: datetime) -> str:
    """The run id for this environment, host and time. Pure, so it is testable."""
    for candidate in (environ.get(ID_VARIABLE), _ci_id(environ)):
        cleaned = sanitize(candidate)
        if cleaned:
            return cleaned
    safe_host = sanitize(host.split(".")[0] if host else "")
    stamp = now.strftime("%Y%m%d-%H%M%S")
    return sanitize(f"local-{safe_host + '-' if safe_host else ''}{stamp}")


def enabled(environ: Mapping[str, str] | None = None) -> bool:
    environ = os.environ if environ is None else environ
    return environ.get(ENABLED_VARIABLE, "true").strip().lower() not in ("0", "false", "no", "off")


def product(environ: Mapping[str, str] | None = None) -> str:
    environ = os.environ if environ is None else environ
    return sanitize(environ.get(PRODUCT_VARIABLE)) or DEFAULT_PRODUCT


def _hostname() -> str:
    try:
        return socket.gethostname()
    except OSError:
        return "unknown"


def run_id(environ: Mapping[str, str] | None = None) -> str:
    """
    The id for this run.

    Against the real process environment the first answer is exported as
    ``TEST_RUN_ID``, so a local id minted from the clock does not change between
    two calls a second apart. An explicit ``environ`` is read, never written.
    """
    if environ is None:
        if not sanitize(os.environ.get(ID_VARIABLE)):
            os.environ[ID_VARIABLE] = resolve(os.environ, _hostname(), datetime.now())
        environ = os.environ
    value = resolve(environ, _hostname(), datetime.now())
    _announce(value, environ)
    return value


def pin_for_session(environ: dict[str, str] | None = None) -> str:
    """
    Resolve the id once and export it, so every later caller and every xdist
    worker spawned after this point reads the same value. Call on the controller.
    """
    if environ is None:
        return run_id()
    value = run_id(environ)
    environ[ID_VARIABLE] = value
    return value


def worker_prefix(environ: Mapping[str, str] | None = None) -> str:
    """This process's worker prefix (``w2``, ``ws``), or ``""`` when not sharded."""
    environ = os.environ if environ is None else environ
    value = (environ.get(WORKER_PREFIX_VARIABLE) or "").strip()
    if not re.fullmatch(r"[a-z0-9]{0,8}", value):
        raise ValueError(
            f"{WORKER_PREFIX_VARIABLE}={value!r} is not a worker prefix: "
            "use up to 8 lower-case letters or digits (w1, ws)"
        )
    return value


def data_tag(tag: str, environ: Mapping[str, str] | None = None) -> str:
    """``tag`` with the worker prefix in front (``w2-1a2b3c4d``); unchanged when not sharded."""
    prefix = worker_prefix(environ)
    return f"{prefix}-{tag}" if prefix else tag


def user_agent_token(environ: Mapping[str, str] | None = None) -> str:
    return f"{product(environ)}/{run_id(environ)}"


def api_headers(
    environ: Mapping[str, str] | None = None, client: str = "python-requests"
) -> dict[str, str]:
    """Headers an HTTP client adds to every request; empty when tagging is off."""
    if not enabled(environ):
        return {}
    return {
        "User-Agent": f"{user_agent_token(environ)} {client}",
        HEADER: run_id(environ),
    }


def browser_user_agent(
    default_user_agent: str | None, environ: Mapping[str, str] | None = None
) -> str | None:
    """
    The browser's default User-Agent plus the run token, or None when tagging
    is off or the default is unknown (None leaves the browser default in place).
    """
    if not enabled(environ) or not default_user_agent or not default_user_agent.strip():
        return None
    return f"{default_user_agent} {user_agent_token(environ)}"


def tagged_user_agent(browser, environ: Mapping[str, str] | None = None) -> str | None:
    """
    Read the browser's real User-Agent from a throwaway page and append the token.

    Reading it rather than hard-coding one keeps the version current and keeps the
    part a WAF looks at exactly as the browser would send it.
    """
    if not enabled(environ):
        return None
    context = browser.new_context()
    try:
        page = context.new_page()
        return browser_user_agent(page.evaluate("() => navigator.userAgent"), environ)
    finally:
        context.close()


def _announce(value: str, environ: Mapping[str, str]) -> None:
    global _announced
    if _announced:
        return
    _announced = True
    if enabled(environ):
        logger.info("[run] tagging test traffic as %s/%s", product(environ), value)
    else:
        logger.info("[run] test traffic tagging switched off (%s=false)", ENABLED_VARIABLE)
