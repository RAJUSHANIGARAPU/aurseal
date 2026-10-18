"""
Which backend calls did that UI action cause? Record them and say exactly.

``BrowserEvidence`` keeps what went *wrong* for the whole test; ``ResponseCapture``
keeps the body of one named call. Neither answers the question a release check
often asks: "clicking Save sent exactly these calls, and nothing to the export
service". ``NetworkRecorder`` does. It listens on a page or a whole context from
``start()`` to ``stop()`` and keeps every matching ``Call`` — method, URL and,
for responses, the status — in the order the browser saw them.

Requests and responses are kept apart on purpose. A request that never got a
response (aborted, blocked, still in flight when the action returned) shows up
in ``requests()`` but not in ``responses()``; ``unanswered()`` lists exactly those,
and ``failed()`` lists the ones the browser reported as failed.

Use it as a context manager so the listeners are removed when the action is done::

    with NetworkRecorder(page, match="/api/") as calls:
        page.get_by_role("button", name="Save").click()
        page.wait_for_load_state("networkidle")
    assert calls.methods_and_paths() == [("PUT", "/api/orders/ORD-42")]
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

from core.logger import logger

DEFAULT_LIMIT = 1000
Matcher = str | re.Pattern[str] | Callable[[str], bool] | None


@dataclass(frozen=True)
class Call:
    method: str
    url: str
    status: int | None = None  # None for a request; the HTTP status for a response
    failure: str | None = None  # the browser's failure text for a failed request

    @property
    def path(self) -> str:
        return urlsplit(self.url).path

    def __str__(self) -> str:
        if self.failure:
            return f"{self.method} {self.url} -> {self.failure}"
        if self.status is None:
            return f"{self.method} {self.url}"
        return f"{self.method} {self.url} -> {self.status}"


def _predicate(match: Matcher) -> Callable[[str], bool]:
    if match is None:
        return lambda url: True
    if isinstance(match, str):
        return lambda url: match in url
    if isinstance(match, re.Pattern):
        return lambda url: match.search(url) is not None
    return match


class NetworkRecorder:
    """Records matching calls on ``target`` (a Playwright page or context)."""

    def __init__(self, target, match: Matcher = None, limit: int = DEFAULT_LIMIT) -> None:
        self._target = target
        self._matches = _predicate(match)
        self._limit = limit
        self._requests: list[Call] = []
        self._responses: list[Call] = []
        self._failed: list[Call] = []
        self._lock = threading.Lock()
        self._recording = False
        self.dropped = 0

    # ── lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> NetworkRecorder:
        if not self._recording:
            self._target.on("request", self.on_request)
            self._target.on("response", self.on_response)
            self._target.on("requestfailed", self.on_request_failed)
            self._recording = True
        return self

    def stop(self) -> NetworkRecorder:
        """Detach; what was recorded stays readable. Never raises."""
        if not self._recording:
            return self
        for event, listener in (
            ("request", self.on_request),
            ("response", self.on_response),
            ("requestfailed", self.on_request_failed),
        ):
            try:
                self._target.remove_listener(event, listener)
            except Exception as exc:  # noqa: BLE001 - a closed page cannot be detached from
                logger.debug("network recorder: could not detach %s: %s", event, exc)
        self._recording = False
        return self

    @property
    def recording(self) -> bool:
        return self._recording

    def __enter__(self) -> NetworkRecorder:
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    # ── listeners (public so they can be driven without a browser) ────────────

    def _keep(self, bucket: list[Call], call: Call) -> None:
        if not self._matches(call.url):
            return
        with self._lock:
            if len(bucket) < self._limit:
                bucket.append(call)
            else:
                self.dropped += 1

    def on_request(self, request) -> None:
        self._keep(self._requests, Call(request.method, request.url))

    def on_response(self, response) -> None:
        self._keep(
            self._responses, Call(response.request.method, response.url, status=response.status)
        )

    def on_request_failed(self, request) -> None:
        failure = request.failure or "failed"
        self._keep(self._failed, Call(request.method, request.url, failure=str(failure)))

    # ── reading ──────────────────────────────────────────────────────────────

    def requests(self) -> list[Call]:
        with self._lock:
            return list(self._requests)

    def responses(self) -> list[Call]:
        with self._lock:
            return list(self._responses)

    def failed(self) -> list[Call]:
        with self._lock:
            return list(self._failed)

    def unanswered(self) -> list[Call]:
        """Requests with no response yet, matched one-for-one by method and URL."""
        pending = [(c.method, c.url) for c in self.responses()]
        result = []
        for call in self.requests():
            key = (call.method, call.url)
            if key in pending:
                pending.remove(key)
            else:
                result.append(call)
        return result

    def methods_and_paths(self, *, responses: bool = False) -> list[tuple[str, str]]:
        """``(METHOD, /path)`` pairs, the shape a "which calls" assertion usually wants."""
        calls = self.responses() if responses else self.requests()
        return [(c.method, c.path) for c in calls]

    def with_status(self, predicate: Callable[[int], bool]) -> list[Call]:
        return [c for c in self.responses() if c.status is not None and predicate(c.status)]

    def describe(self) -> str:
        """A readable block for an assertion message."""
        lines = [str(c) for c in self.responses()]
        lines += [f"{c}  (no response)" for c in self.unanswered()]
        lines += [str(c) for c in self.failed()]
        if self.dropped:
            lines.append(f"... {self.dropped} more calls not kept (limit {self._limit})")
        return "\n".join(lines) or "(no matching calls)"

    def clear(self) -> None:
        with self._lock:
            self._requests.clear()
            self._responses.clear()
            self._failed.clear()
            self.dropped = 0
