"""
What the browser said while a test ran: console errors, page errors, failed requests.

The root ``conftest.py`` used to keep these in two module-level lists cleared at
the start of each test and attached only to the first ``page``. A popup or a
second tab reported nothing, and uncaught page exceptions (``pageerror``) were not
collected at all although they are the most direct evidence a front end broke.

``BrowserEvidence`` is per test and listens on the **context**, so every page the
test opens is covered, including ones opened by the app. Each request is reported
once, as ``METHOD url -> status`` for an HTTP error or ``METHOD url -> <failure>``
for a request that never got a response (DNS, reset, aborted).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from core.logger import logger

DEFAULT_LIMIT = 200


@dataclass
class BrowserEvidence:
    console_errors: list[str] = field(default_factory=list)
    page_errors: list[str] = field(default_factory=list)
    failed_requests: list[str] = field(default_factory=list)
    last_screenshot: bytes | None = None
    limit: int = DEFAULT_LIMIT

    def _add(self, bucket: list[str], entry: str) -> None:
        # A page that logs an error per animation frame must not make the bundle unbounded.
        if len(bucket) < self.limit:
            bucket.append(entry)

    # ── wiring ───────────────────────────────────────────────────────────────

    def attach_context(self, context) -> BrowserEvidence:
        """Collect from every page of ``context``, existing and future."""
        for page in list(context.pages):
            self.attach_page(page)
        context.on("page", self.attach_page)
        return self

    def attach_page(self, page) -> BrowserEvidence:
        page.on("console", self.on_console)
        page.on("pageerror", self.on_page_error)
        page.on("response", self.on_response)
        page.on("requestfailed", self.on_request_failed)
        return self

    # ── listeners (public so they can be driven without a browser) ────────────

    def on_console(self, message) -> None:
        if message.type == "error":
            self._add(self.console_errors, message.text)

    def on_page_error(self, error) -> None:
        self._add(self.page_errors, str(error))

    def on_response(self, response) -> None:
        if response.status >= 400:
            entry = f"{response.request.method} {response.url} -> {response.status}"
            self._add(self.failed_requests, entry)

    def on_request_failed(self, request) -> None:
        failure = request.failure or "failed"
        self._add(self.failed_requests, f"{request.method} {request.url} -> {failure}")

    # ── capture ──────────────────────────────────────────────────────────────

    def screenshot(
        self, page, name: str, directory: str | Path = "reports/screenshots"
    ) -> Path | None:
        """Full-page PNG saved under ``directory``; bytes kept for the failure bundle."""
        safe = "".join(c if c.isalnum() or c in "_-" else "_" for c in name)
        path = Path(directory) / f"{safe}-{time.strftime('%Y%m%d-%H%M%S')}.png"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.last_screenshot = page.screenshot(path=str(path), full_page=True)
            return path
        except Exception as exc:  # noqa: BLE001 - evidence capture never fails a test
            logger.warning("screenshot for %s failed: %s", name, exc)
            return None

    def clear(self) -> None:
        self.console_errors.clear()
        self.page_errors.clear()
        self.failed_requests.clear()
        self.last_screenshot = None

    def as_dict(self) -> dict[str, list[str]]:
        return {
            "consoleErrors": list(self.console_errors),
            "pageErrors": list(self.page_errors),
            "failedRequests": list(self.failed_requests),
        }
