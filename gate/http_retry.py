"""Gateway-error retry policy: when a 5xx may be re-sent, and when the run abstains.

Transport-agnostic and pure, so the same rules drive a browser route handler
and an API client. The browser wiring (a ``page.route`` handler that calls
:func:`fetch_with_retry` around ``route.fetch()``) lives with the browser
layer; this module only decides.

The rules:

* A ``500`` is never retried. It is the server's own answer and often
  deterministic; re-sending a write after deterministic 500s sends it again
  and again.
* A ``502/503/504`` (a gateway that did not get an answer) is retried — but
  only on a call that is safe to send twice: a read (GET/HEAD/OPTIONS), or a
  write the server deduplicates (``is_deduplicated``, e.g. an
  optimistic-concurrency write carrying a version, whose duplicate is refused
  with 409 instead of being applied twice). Every other write is sent once.
* Streaming and binary endpoints are never taken over: re-buffering a
  download or an event stream through a fetch corrupts or hangs it.
* Once a write has been fetched, a failure afterwards *aborts* it rather than
  resuming the original request, which would send it a second time. Only a
  read is resumed.
* Every discarded 5xx is handed to ``on_discard`` (the circuit breaker), since
  a retried response never reaches the page's own listeners. A response that
  is still a gateway error after the last retry is ``ABSTAIN``: the caller
  delivers it and the breaker, not the test, decides what the run does next.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from urllib.parse import parse_qs, urlsplit

from gate._env import env_float, env_int

RETRIES_ENV = "HTTP_RETRY_5XX"
BACKOFF_ENV = "HTTP_RETRY_BACKOFF_S"

UPSTREAM_GATEWAY_CODES = frozenset({502, 503, 504})
READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
STREAMING_FRAGMENTS = ("/download", "/stream", "/events", "/sse")


def versioned_write(method: str, url: str, param: str = "version") -> bool:
    """A write carrying an optimistic-concurrency version in its query string."""
    if method.upper() in READ_METHODS:
        return False
    values = parse_qs(urlsplit(url).query).get(param)
    return bool(values and values[0].strip())


class Decision(str, Enum):
    RETRY = "retry"
    DELIVER = "deliver"  # hand this response to the caller
    ABSTAIN = "abstain"  # still a gateway error after the last retry: deliver, let the breaker act


class AfterFailure(str, Enum):
    RESUME = "resume"  # let the original request go through untouched
    ABORT = "abort"  # the request may already have been sent; never send it again


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int = 3
    backoff_s: float = 1.5  # linear: backoff_s * attempt
    is_deduplicated: Callable[[str, str], bool] = versioned_write
    streaming_fragments: tuple[str, ...] = STREAMING_FRAGMENTS
    # Which calls the handler takes over at all. Routing every read through a
    # fetch serialises traffic the browser would parallelise; narrow it here.
    protects_predicate: Callable[[str, str], bool] | None = field(default=None)

    @classmethod
    def from_env(cls, **kwargs: Any) -> RetryPolicy:
        return cls(
            max_retries=env_int(RETRIES_ENV, 3), backoff_s=env_float(BACKOFF_ENV, 1.5), **kwargs
        )

    def safe_to_repeat(self, method: str, url: str) -> bool:
        return method.upper() in READ_METHODS or self.is_deduplicated(method, url)

    def protects(self, method: str, url: str) -> bool:
        if self.max_retries <= 0 or any(f in url for f in self.streaming_fragments):
            return False
        if self.protects_predicate is not None:
            return self.protects_predicate(method, url)
        return self.safe_to_repeat(method, url)

    def decide(self, method: str, url: str, status: int, attempt: int) -> Decision:
        """``attempt`` is how many retries have already been made for this call."""
        if status not in UPSTREAM_GATEWAY_CODES or not self.safe_to_repeat(method, url):
            return Decision.DELIVER
        if attempt < self.max_retries:
            return Decision.RETRY
        return Decision.ABSTAIN

    @staticmethod
    def after_fetch_failure(method: str) -> AfterFailure:
        return AfterFailure.RESUME if method.upper() in READ_METHODS else AfterFailure.ABORT

    def delay(self, attempt: int) -> float:
        return self.backoff_s * attempt


@dataclass(frozen=True)
class Outcome:
    status: int
    response: Any
    retries: int
    decision: Decision


def fetch_with_retry(
    fetch: Callable[[], Any],
    method: str,
    url: str,
    policy: RetryPolicy,
    *,
    status_of: Callable[[Any], int] = lambda r: r.status,
    on_discard: Callable[[int, int], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Outcome:
    """Fetch once, then re-fetch while the policy says RETRY.

    The first fetch is not guarded: if it raises, the caller decides (see
    :meth:`RetryPolicy.after_fetch_failure`). A re-fetch that raises ends the
    loop with the last response seen. ``on_discard(attempt, status)`` sees each
    response that is thrown away — the only place it is ever seen.
    """
    response = fetch()
    attempt = 0
    while True:
        status = status_of(response)
        decision = policy.decide(method, url, status, attempt)
        if decision is not Decision.RETRY:
            return Outcome(status, response, attempt, decision)
        attempt += 1
        if on_discard is not None:
            on_discard(attempt, status)
        sleep(policy.delay(attempt))
        try:
            response = fetch()
        except Exception:
            return Outcome(status, response, attempt, Decision.ABSTAIN)
