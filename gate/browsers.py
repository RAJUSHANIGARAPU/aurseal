"""Browser identity of a test: which engine a node id ran on, and the id without it.

pytest-playwright parametrises browser tests by engine, so one test collected for
three engines has three node ids: ``test_x[chromium]``, ``test_x[firefox]``,
``test_x[webkit]``. For anything that tracks a *test* over time — flakiness
history, the CI-selection gate, case identity in the results store — those are
one test that ran three times, so the engine has to come off the id. For
anything that reports a *result*, the engine has to stay with it, so a failure on
webkit alone reads as a webkit failure.
"""

from __future__ import annotations

import re

SUPPORTED: tuple[str, ...] = ("chromium", "firefox", "webkit")
_PARAMS_RE = re.compile(r"^(?P<head>.*?)\[(?P<params>.*)\]$")


def _split(nodeid: str) -> tuple[str, list[str]] | None:
    match = _PARAMS_RE.match(nodeid)
    if not match:
        return None
    return match.group("head"), match.group("params").split("-")


def browser_from_nodeid(nodeid: str) -> str | None:
    """The engine named in a node id's parameters, or None for a non-browser id."""
    parts = _split(nodeid)
    if parts is None:
        return None
    found = [p for p in parts[1] if p in SUPPORTED]
    return found[-1] if found else None


def strip_browser_param(nodeid: str) -> str:
    """The node id with the engine removed: ``t[a-firefox]`` -> ``t[a]``, ``t[webkit]`` -> ``t``."""
    parts = _split(nodeid)
    if parts is None:
        return nodeid
    head, params = parts
    kept = [p for p in params if p not in SUPPORTED]
    if len(kept) == len(params):
        return nodeid
    return f"{head}[{'-'.join(kept)}]" if kept else head


def item_browser(item) -> str | None:
    """Engine of a collected pytest item: the ``browser_name`` parameter, else its node id."""
    callspec = getattr(item, "callspec", None)
    params = getattr(callspec, "params", {}) or {}
    value = params.get("browser_name")
    if isinstance(value, str) and value:
        return value
    return browser_from_nodeid(getattr(item, "nodeid", ""))
