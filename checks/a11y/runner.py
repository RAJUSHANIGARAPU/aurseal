"""Scan a list of routes in a browser this module starts (plugin and CLI use)."""

from __future__ import annotations

from collections.abc import Sequence

from checks._browser import browser_context
from checks.a11y.axe import A11yReport, axe_source, scan
from checks.a11y.policy import A11yPolicy


def scan_routes(
    routes: Sequence[str],
    policy: A11yPolicy | None = None,
    browser_name: str = "chromium",
    *,
    channel: str | None = None,
    timeout_ms: int = 30_000,
) -> list[A11yReport]:
    """One report per route. A route that does not load is a report with an ``error``.

    Raises ``AxeUnavailable`` (no axe script) or ``BrowserUnavailable`` (no browser)
    before any navigation, so the caller can tell "could not scan" from "scanned".
    """
    source = axe_source()
    reports: list[A11yReport] = []
    with browser_context(browser_name, channel=channel) as context:
        page = context.new_page()
        for route in routes:
            try:
                response = page.goto(route, wait_until="load", timeout=timeout_ms)
            except Exception as exc:  # noqa: BLE001
                reports.append(A11yReport(route=route, error=f"unreachable: {exc}"))
                continue
            if response is not None and response.status >= 400:
                reports.append(A11yReport(route=route, error=f"answered {response.status}"))
                continue
            reports.append(scan(page, policy, route=route, source=source))
    return reports
