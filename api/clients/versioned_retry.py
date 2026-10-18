"""Retry of version-guarded write actions on gateway errors — only when it is provably safe.

A write that carries an optimistic-lock version (``?version=N``) runs in one
transaction: it either commits and moves the entity past ``N``, or changes
nothing. Re-sending it with the same ``N`` is therefore safe *only because* a
duplicate is refused (``409``) instead of applied twice.

The rule that makes the retry safe:

* Only 502/503/504 and a client-side *read* timeout are retried. They mean
  "the gateway gave up", and the request may still have committed behind it.
  A 500 is the service itself and may be a deterministic product error; any
  4xx is an answer. Both are returned as-is. A *connect* timeout means the
  request never left, so it propagates rather than being waited out.
* A gateway error triggers a fresh read of the entity once the backoff has passed,
  giving an in-flight request time to finish. A version beyond ``N``
  proves the first attempt was applied: the fresh entity is returned and the
  call is **not** re-sent. Only when the version is still ``N`` (or unreadable)
  is the call re-sent with the same ``N``.
* A 409 returned for a retry gets the same re-read, because that is what a
  delayed original request that already went through looks like.

Calls without a version token, creates, and anything the matcher does not
recognise are never retried — there is nothing that proves the first attempt
did not land.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit

import requests

if TYPE_CHECKING:
    from api.clients.http_engine import ApiResponse

logger = logging.getLogger(__name__)

UPSTREAM_GATEWAY_CODES = frozenset({502, 503, 504})
CONFLICT = 409


@dataclass(frozen=True)
class VersionedTarget:
    """A version-guarded call: where to re-read the entity, and the version it was sent at."""

    entity_path: str
    sent_version: int


Matcher = Callable[[str, str], "VersionedTarget | None"]


def query_version_matcher(
    entity_pattern: str = r"^(?P<entity>.*/(?:entities|items)/[^/?]+)/[^?]+$",
    *,
    param: str = "version",
    methods: frozenset[str] = frozenset({"PUT", "POST", "PATCH"}),
    exclude: tuple[str, ...] = (),
) -> Matcher:
    """A matcher for ``<entity>/<action>?version=N`` calls.

    ``entity_pattern`` must define a named group ``entity``: the path the entity
    is re-read from. URLs containing any ``exclude`` substring are never matched.
    """
    compiled = re.compile(entity_pattern)

    def match(method: str, url: str) -> VersionedTarget | None:
        if method.upper() not in methods or any(x in url for x in exclude):
            return None
        parts = urlsplit(url)
        values = parse_qs(parts.query).get(param)
        if not values or not values[0].isdigit():
            return None
        m = compiled.match(parts.path)
        if not m:
            return None
        return VersionedTarget(m.group("entity"), int(values[0]))

    return match


def is_gateway_error(status: int) -> bool:
    return status in UPSTREAM_GATEWAY_CODES


def is_request_timeout(exc: BaseException) -> bool:
    """Sent but unanswered (read timeout).

    A connect timeout never went out, so it does not count.
    """
    return isinstance(exc, requests.exceptions.ReadTimeout) and not isinstance(
        exc, requests.exceptions.ConnectTimeout
    )


def version_reader(field: str = "version") -> Callable[[ApiResponse], int]:
    """Reads the entity version from a successful JSON response; -1 when absent or unreadable."""

    def read(response: ApiResponse) -> int:
        if response is None or not response.is_success:
            return -1
        try:
            value = response.json().get(field)
        except (ValueError, AttributeError):
            return -1
        return value if isinstance(value, int) and not isinstance(value, bool) else -1

    return read


@dataclass
class _Attempt:
    response: ApiResponse | None
    timeout: BaseException | None = None

    @classmethod
    def of(cls, send: Callable[[], ApiResponse]) -> _Attempt:
        try:
            return cls(send())
        except requests.exceptions.RequestException as exc:
            if is_request_timeout(exc):
                return cls(None, exc)
            raise

    def describe(self) -> str:
        return "request timeout" if self.timeout else f"HTTP {self.response.status}"

    def result(self) -> ApiResponse:
        if self.timeout is not None:
            raise self.timeout
        return self.response


def execute(
    target: VersionedTarget,
    send: Callable[[], ApiResponse],
    read_entity: Callable[[], ApiResponse],
    *,
    read_version: Callable[[ApiResponse], int],
    max_retries: int = 2,
    backoff_s: float = 5.0,
    sleep: Callable[[float], None] = time.sleep,
) -> ApiResponse:
    """Send ``send``; on a gateway error decide between "applied" and "re-send".

    Returns the response the caller should act on: the action's own response,
    or the freshly read entity when the version proves the action landed.
    """
    last = _Attempt.of(send)
    attempt = 1
    while True:
        gateway = last.timeout is not None or is_gateway_error(last.response.status)
        conflict_on_resend = (
            attempt > 1 and last.timeout is None and last.response.status == CONFLICT
        )
        if not gateway and not conflict_on_resend:
            return last.response

        sleep(backoff_s * attempt)
        current = read_entity()
        version = read_version(current)
        if version > target.sent_version:
            logger.warning(
                "%s on %s at version %d: applied (now at %d), not re-sending",
                last.describe(),
                target.entity_path,
                target.sent_version,
                version,
            )
            return current
        if conflict_on_resend or attempt > max_retries:
            if gateway:
                logger.warning(
                    "%s on %s at version %d: giving up after %d re-send(s)",
                    last.describe(),
                    target.entity_path,
                    target.sent_version,
                    attempt - 1,
                )
            return last.result()
        logger.warning(
            "%s on %s at version %d: not applied (%s), re-sending %d/%d",
            last.describe(),
            target.entity_path,
            target.sent_version,
            "unreadable" if version < 0 else f"still at {version}",
            attempt,
            max_retries,
        )
        last = _Attempt.of(send)
        attempt += 1
