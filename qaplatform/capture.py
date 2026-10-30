"""Browser capture for an exploratory session: a Playwright trace plus the errors the
browser saw, attached to the session as notes when the capture closes.

:func:`open_capture` wraps a Playwright ``BrowserContext`` the tester is driving: it starts
tracing (screenshots and DOM snapshots) and listens on every page for console errors,
uncaught page errors, failed requests and 5xx responses. :meth:`CaptureHandle.close` stops
the trace into ``reports/sessions/`` and posts one note per collected error (capped) plus
one note carrying the trace as its attachment. Closing is safe to repeat and runs on a
crash too (use the handle as a context manager): whatever was captured is still posted.

:func:`capture_in_browser` is the headed convenience: it opens a browser on a URL and
captures until the tester closes the window. Playwright is imported lazily.

One browser context is recorded; this is not a screen recorder.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from core.artifact_redaction import cookie_values, redact_trace

DEFAULT_DIR = Path("reports") / "sessions"
MAX_ERROR_NOTES = 20
_TEXT_LIMIT = 500

NoteSink = Callable[[dict[str, Any]], Any]


class CaptureHandle:
    def __init__(
        self,
        context: Any,
        session_id: str,
        note: NoteSink,
        *,
        artifact_dir: str | Path = DEFAULT_DIR,
        max_errors: int = MAX_ERROR_NOTES,
    ) -> None:
        self.context = context
        self.session_id = session_id
        self._note = note
        self.artifact_dir = Path(artifact_dir)
        self.max_errors = max_errors
        self.errors: list[str] = []
        self.trace_path: Path | None = None
        self.closed = False
        self.posted: list[dict[str, Any]] = []
        self._watched: set[int] = set()

    # ------------------------------------------------------------------ listening

    def _add(self, text: str) -> None:
        self.errors.append(text[:_TEXT_LIMIT])

    def watch(self, page: Any) -> None:
        if id(page) in self._watched:
            return
        self._watched.add(id(page))

        def on_console(msg: Any) -> None:
            if getattr(msg, "type", "") == "error":
                self._add(f"console error: {msg.text}")

        def on_response(resp: Any) -> None:
            if int(getattr(resp, "status", 0)) >= 500:
                self._add(f"HTTP {resp.status} from {resp.url}")

        def on_request_failed(req: Any) -> None:
            failure = getattr(req, "failure", None)
            self._add(f"request failed: {req.url} ({failure or 'no detail'})")

        page.on("console", on_console)
        page.on("pageerror", lambda exc: self._add(f"page error: {exc}"))
        page.on("requestfailed", on_request_failed)
        page.on("response", on_response)

    def start(self) -> CaptureHandle:
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        self.context.tracing.start(screenshots=True, snapshots=True)
        for page in list(getattr(self.context, "pages", []) or []):
            self.watch(page)
        self.context.on("page", self.watch)
        return self

    # ------------------------------------------------------------------ closing

    def close(self) -> list[dict[str, Any]]:
        """Stop the trace and post what was captured; returns the notes posted."""
        if self.closed:
            return []
        self.closed = True
        posted: list[dict[str, Any]] = []
        target = self.artifact_dir / f"session-{self.session_id}.zip"
        trace_error = ""
        try:
            values = cookie_values(self.context)
            self.context.tracing.stop(path=str(target))
            if redact_trace(target, values):
                self.trace_path = target
            else:
                trace_error = "trace deleted: redaction failed"
        except Exception as exc:  # the browser died: keep going, post what exists
            trace_error = f"{type(exc).__name__}: {exc}"[:_TEXT_LIMIT]
        notes: list[dict[str, Any]] = [
            {"kind": "observation", "text": f"browser {text}"}
            for text in self.errors[: self.max_errors]
        ]
        if len(self.errors) > self.max_errors:
            notes.append(
                {
                    "kind": "observation",
                    "text": f"{len(self.errors) - self.max_errors} more browser error(s) "
                    "not posted; see the trace",
                }
            )
        if self.trace_path is not None:
            notes.append(
                {
                    "kind": "observation",
                    "text": f"browser trace ({len(self.errors)} error(s) captured)",
                    "attachment_uri": str(self.trace_path),
                }
            )
        else:
            notes.append({"kind": "observation", "text": f"trace not saved: {trace_error}"})
        for note in notes:
            body = {"schema_version": "1.0", **note}
            self._note(body)
            posted.append(body)
        self.posted = posted
        return posted

    def __enter__(self) -> CaptureHandle:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def client_sink(client: Any, session_id: str) -> NoteSink:
    """Post notes through a :class:`~qaplatform.client.PlatformClient`."""
    return lambda body: client.session_note(session_id, body)


def open_capture(
    client: Any,
    session_id: str,
    context: Any,
    *,
    artifact_dir: str | Path = DEFAULT_DIR,
) -> CaptureHandle:
    return CaptureHandle(
        context, session_id, client_sink(client, session_id), artifact_dir=artifact_dir
    ).start()


def capture_in_browser(
    client: Any,
    session_id: str,
    url: str,
    *,
    artifact_dir: str | Path = DEFAULT_DIR,
    headless: bool = False,
    wait: Callable[[Any], None] | None = None,
) -> list[dict[str, Any]]:
    """Open a browser on ``url``, capture until the tester closes the page, then post."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context()
        try:
            with open_capture(client, session_id, context, artifact_dir=artifact_dir) as handle:
                page = context.new_page()
                page.goto(url)
                (wait or (lambda p: p.wait_for_event("close", timeout=0)))(page)
            return handle.posted
        finally:
            browser.close()


__all__ = ["CaptureHandle", "capture_in_browser", "client_sink", "open_capture"]
