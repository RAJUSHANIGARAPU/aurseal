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
  "failedRequests": ["POST /api/auth/login → 401"]
}
"""

import base64
import json
import traceback
from datetime import datetime, timezone
from pathlib import Path


def write_failure_bundle(
    test_name: str,
    error: BaseException,
    screenshot_bytes: bytes | None,
    console_errors: list[str],
    failed_requests: list[str],
    output_dir: str = "reports/failures",
    dom_snapshot: str = "",
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
            "stackTrace": traceback.format_exc(),
            "screenshot": base64.b64encode(screenshot_bytes).decode() if screenshot_bytes else "",
            "consoleErrors": console_errors,
            "failedRequests": failed_requests,
            "domSnapshot": dom_snapshot,
        }

        out_file.write_text(json.dumps(bundle, indent=2), encoding="utf-8")
        return out_file

    except Exception as exc:  # noqa: BLE001
        print(f"[failure_reporter] Could not write bundle for '{test_name}': {exc}")
        return None
