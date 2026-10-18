"""
Playwright wiring for retrying a browser request that hit a transient gateway error.

This module is only the *plumbing*: a route handler that fetches the request,
asks an injected policy whether the response deserves another attempt, and hands
the page the final response. *Whether* a given method/URL/status is safe and worth
repeating is a policy question and is deliberately not decided here — pass
``protects`` (should this request be routed through the retry at all?) and
``should_retry`` (is this response worth another attempt?).

Two rules belong to the plumbing because they are about not sending a write twice:

* if ``route.fetch()`` itself fails, a GET is resumed (sent normally); anything
  else is **aborted** — the request may already have reached the server, and
  resuming would send it a second time;
* if fulfilling the page fails after the fetch, the route is aborted for the same
  reason.

Streaming and download responses should be excluded by ``protects``: buffering
them through ``route.fetch()`` corrupts or hangs them.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from core.logger import logger

Protects = Callable[[str, str], bool]  # (method, url) -> route it through the retry?
ShouldRetry = Callable[[str, str, int], bool]  # (method, url, status) -> try again?


@dataclass
class RetryOutcome:
    response: object
    retries: int


def fetch_with_retry(
    fetch: Callable[[], object],
    status_of: Callable[[object], int],
    retryable: Callable[[int], bool],
    max_retries: int,
    backoff_s: float,
    sleep: Callable[[float], None] = time.sleep,
    on_retry: Callable[[int, int], None] | None = None,
) -> RetryOutcome:
    """Fetch, then re-fetch while ``retryable(status)`` and attempts remain (linear backoff)."""
    response = fetch()
    attempt = 0
    while retryable(status_of(response)) and attempt < max_retries:
        attempt += 1
        if on_retry:
            on_retry(attempt, status_of(response))
        sleep(backoff_s * attempt)
        try:
            response = fetch()
        except Exception as exc:  # noqa: BLE001 - keep the last real response
            logger.warning("[resilience] retry %d fetch failed: %s", attempt, exc)
            break
    return RetryOutcome(response, attempt)


def make_route_handler(
    protects: Protects,
    should_retry: ShouldRetry,
    max_retries: int = 3,
    backoff_s: float = 1.5,
    sleep: Callable[[float], None] = time.sleep,
) -> Callable[[object], None]:
    """A ``page.route``/``context.route`` handler applying the injected policy."""

    def handle(route) -> None:
        request = route.request
        method, url = request.method, request.url
        if not protects(method, url):
            route.fallback()
            return
        try:
            outcome = fetch_with_retry(
                route.fetch,
                lambda response: response.status,
                lambda status: should_retry(method, url, status),
                max_retries,
                backoff_s,
                sleep,
                lambda attempt, status: logger.warning(
                    "[resilience] HTTP %d on %s %s, retry %d/%d",
                    status,
                    method,
                    url,
                    attempt,
                    max_retries,
                ),
            )
        except Exception as exc:  # noqa: BLE001
            if method == "GET":
                logger.debug("[resilience] fetch failed, resuming GET %s: %s", url, exc)
                route.fallback()
            else:
                logger.warning(
                    "[resilience] fetch of %s %s failed (%s); aborting, not resending",
                    method,
                    url,
                    exc,
                )
                _abort(route)
            return
        if outcome.retries:
            logger.info(
                "[resilience] %s %s answered HTTP %d after %d retry(ies)",
                method,
                url,
                outcome.response.status,
                outcome.retries,
            )
        try:
            route.fulfill(response=outcome.response)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[resilience] fulfilling %s %s failed (%s); aborting, not resending",
                method,
                url,
                exc,
            )
            _abort(route)

    return handle


def install(
    target, protects: Protects, should_retry: ShouldRetry, max_retries: int = 3, **kwargs
) -> None:
    """Route every request of a page or context through the handler; no-op when max_retries <= 0."""
    if max_retries <= 0:
        return
    target.route("**/*", make_route_handler(protects, should_retry, max_retries, **kwargs))


def _abort(route) -> None:
    try:
        route.abort("failed")
    except Exception as exc:  # noqa: BLE001 - already handled
        logger.debug("[resilience] abort failed: %s", exc)
