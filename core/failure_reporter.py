"""
Writes a structured JSON failure bundle to reports/failures/<test>-<ts>.json
whenever a test fails. Designed to be consumed by a local AI agent for root-cause
analysis without requiring trace ZIP downloads or video playback.

Bundle schema:
{
  "test":           "test_login_flow",
  "timestamp":      "2026-04-24T09:31:00Z",
  "error":          "AssertionError: expected 'Dashboard' to be in page title",
  "stackTrace":     "...",
  "screenshot":     "<base64 PNG>",
  "consoleErrors":  ["TypeError: Cannot read properties of null"],
  "failedRequests": ["POST /api/auth/login → 401"],
  "context":        {"runId": "...", "deployedVersion": "...", "trace": "..."}
}

The stack trace is taken from the exception itself. ``traceback.format_exc()``
only knows about an exception currently being handled, and the bundle is written
from a report hook long after the test's ``except`` is gone, so it used to record
``NoneType: None`` for every failure. The trace is capped at ``MAX_STACK_CHARS``
from the end, where the frames that failed are.
"""

import base64
import json
import traceback
from datetime import datetime, timezone
from pathlib import Path

from core.logger import logger

MAX_STACK_CHARS = 4000


def format_stack(error: BaseException) -> str:
    """The exception's own traceback, keeping the last ``MAX_STACK_CHARS`` characters."""
    text = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    if len(text) <= MAX_STACK_CHARS:
        return text
    return "...[truncated]\n" + text[-MAX_STACK_CHARS:]


def write_failure_bundle(
    test_name: str,
    error: BaseException,
    screenshot_bytes: bytes | None,
    console_errors: list[str],
    failed_requests: list[str],
    output_dir: str = "reports/failures",
    dom_snapshot: str = "",
    page_errors: list[str] | None = None,
    context: dict[str, str] | None = None,
) -> Path | None:
    safe_name = "".join(c if c.isalnum() or c in "_-" else "_" for c in test_name)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(output_dir)
    out_file = out_dir / f"{safe_name}-{ts}.json"

    try:
        out_dir.mkdir(parents=True, exist_ok=True)

        bundle = {
            "test": test_name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "error": str(error),
            "errorType": type(error).__name__,
            "stackTrace": format_stack(error),
            "screenshot": base64.b64encode(screenshot_bytes).decode() if screenshot_bytes else "",
            "consoleErrors": console_errors,
            "pageErrors": page_errors or [],
            "failedRequests": failed_requests,
            "domSnapshot": dom_snapshot,
            "context": context or {},
        }

        out_file.write_text(json.dumps(bundle, indent=2), encoding="utf-8")
        return out_file

    except Exception as exc:  # noqa: BLE001
        logger.warning("[failure_reporter] Could not write bundle for '%s': %s", test_name, exc)
        return None
