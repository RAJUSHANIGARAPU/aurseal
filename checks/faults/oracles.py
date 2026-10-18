"""What "handled the failure well" means, as checks that return a result instead of raising.

Each oracle returns ``OracleResult(name, passed, detail)``; ``OracleResult.check()`` raises
``AssertionError`` with the detail when it did not pass.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class OracleResult:
    name: str
    passed: bool
    detail: str = ""

    def check(self) -> OracleResult:
        if not self.passed:
            raise AssertionError(f"{self.name}: {self.detail}")
        return self


def no_blank_page(page: Any, min_chars: int = 1) -> OracleResult:
    """The page still shows something: a failure must not leave a white screen."""
    text = (page.locator("body").inner_text() or "").strip()
    return OracleResult(
        "no_blank_page", len(text) >= min_chars, f"body text has {len(text)} characters"
    )


def error_is_shown(locator: Any, timeout_ms: int = 5000) -> OracleResult:
    """A user-facing error appears (visible, with text)."""
    try:
        locator.wait_for(state="visible", timeout=timeout_ms)
    except Exception as exc:  # noqa: BLE001
        return OracleResult("error_is_shown", False, f"no visible error: {exc}")
    text = (locator.inner_text() or "").strip()
    return OracleResult("error_is_shown", bool(text), f"error text: {text!r}")


def no_uncaught_page_errors(page_errors: Iterable[str]) -> OracleResult:
    """No uncaught script error escaped (pass ``BrowserEvidence.page_errors``)."""
    errors = list(page_errors)
    return OracleResult("no_uncaught_page_errors", not errors, f"page errors: {errors[:5]}")


def retries_bounded(handle: Any, max_n: int, spec_id: str | None = None) -> OracleResult:
    """The client did not hammer the failing endpoint: at most ``max_n`` matching requests."""
    seen = handle.seen(spec_id) if hasattr(handle, "seen") else handle.plan.seen(spec_id)
    return OracleResult("retries_bounded", seen <= max_n, f"{seen} request(s), limit {max_n}")


def recovers_after(
    handle: Any, action: Callable[[], Any], check: Callable[[], OracleResult | bool]
) -> OracleResult:
    """Remove the fault, run ``action`` (reload, retry), and the flow works again."""
    handle.remove()
    action()
    try:
        result = check()
    except AssertionError as exc:
        return OracleResult("recovers_after", False, str(exc))
    passed = result.passed if isinstance(result, OracleResult) else bool(result)
    detail = result.detail if isinstance(result, OracleResult) else ""
    return OracleResult("recovers_after", passed, detail)
