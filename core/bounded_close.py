"""
Teardown calls under a deadline, so a dead browser cannot stall the run.

``Browser.close()`` and ``BrowserContext.close()`` are protocol round-trips: the
client writes to the driver pipe and waits for a reply. When the browser process
has died, no reply ever comes and the call waits forever. Nothing is raised, so a
``try/except`` around the close is no protection at all; only a deadline is. If
that close sits in a fixture's setup (a login helper closing its throwaway
browser), it freezes every test in the run, not one.

Two entry points, because Playwright's two APIs need different mechanisms:

* ``aclose_within`` — for the **async** API. ``asyncio.wait_for`` cancels the await
  at the deadline, which is exactly the bound needed.
* ``close_within`` — for anything whose close may be called from another thread:
  the call runs on a *daemon* thread and is abandoned at the deadline. Daemon
  matters: an abandoned close stays parked for the life of the process, and a
  non-daemon thread would move the hang from the start of the run to its exit.

The **sync** Playwright API is greenlet-bound to the thread that created it and
refuses calls from any other thread, so ``close_within`` cannot bound a sync
``browser.close()``. For sync code the bound belongs at the process level
(``pytest-timeout``, or ``faulthandler`` dumps); ``close_within`` still applies
to the non-Playwright resources a teardown releases (HTTP sessions, tunnels, a
recorder process).

Failures are logged at WARNING naming the resource and never raised: teardown is
not the place to fail a test.
"""

from __future__ import annotations

import asyncio
import itertools
import os
import threading
from collections.abc import Awaitable, Callable

from core.logger import logger

DEFAULT_TIMEOUT_S = 10.0
TIMEOUT_VARIABLE = "PLAYWRIGHT_CLOSE_TIMEOUT_MS"

_sequence = itertools.count(1)


def configured_timeout_s(environ: dict[str, str] | None = None) -> float:
    """The deadline in seconds; the default on a missing, non-numeric or non-positive value."""
    raw = (os.environ if environ is None else environ).get(TIMEOUT_VARIABLE, "").strip()
    if not raw:
        return DEFAULT_TIMEOUT_S
    try:
        millis = float(raw)
    except ValueError:
        logger.warning("%s=%s is not a number, using %ss", TIMEOUT_VARIABLE, raw, DEFAULT_TIMEOUT_S)
        return DEFAULT_TIMEOUT_S
    if millis <= 0:
        logger.warning("%s=%s is not positive, using %ss", TIMEOUT_VARIABLE, raw, DEFAULT_TIMEOUT_S)
        return DEFAULT_TIMEOUT_S
    return millis / 1000.0


def close_within(
    label: str, action: Callable[[], object] | None, timeout_s: float | None = None
) -> bool:
    """
    Run ``action`` on a daemon thread and wait at most ``timeout_s``.

    Returns True when it finished in time without raising. ``None`` is a no-op
    that counts as success, so callers can pass an optional resource's close.
    """
    if action is None:
        return True
    deadline = configured_timeout_s() if timeout_s is None else timeout_s
    outcome: dict[str, BaseException] = {}

    def target() -> None:
        try:
            action()
        except BaseException as exc:  # noqa: BLE001 - reported below, never raised
            outcome["error"] = exc

    worker = threading.Thread(target=target, name=f"bounded-close-{next(_sequence)}", daemon=True)
    worker.start()
    worker.join(deadline)
    if worker.is_alive():
        logger.warning(
            "Abandoning %s close after %.1fs: it did not return. The process behind it is most "
            "likely gone. Continuing without it; increase %s for a slower environment.",
            label,
            deadline,
            TIMEOUT_VARIABLE,
        )
        return False
    if "error" in outcome:
        logger.warning("Error closing %s: %s", label, outcome["error"])
        return False
    return True


async def aclose_within(
    label: str, close: Callable[[], Awaitable[object]] | None, timeout_s: float | None = None
) -> bool:
    """The async counterpart: await ``close()`` for at most ``timeout_s``."""
    if close is None:
        return True
    deadline = configured_timeout_s() if timeout_s is None else timeout_s
    try:
        await asyncio.wait_for(close(), timeout=deadline)
        return True
    except asyncio.TimeoutError:
        logger.warning("Abandoning %s close after %.1fs: it did not return.", label, deadline)
        return False
    except Exception as exc:  # noqa: BLE001 - teardown never raises
        logger.warning("Error closing %s: %s", label, exc)
        return False
