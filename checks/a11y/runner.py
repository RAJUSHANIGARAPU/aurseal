"""Scan a list of routes in a browser this module starts (plugin and CLI use).

Two rails keep "no violations" honest:

* **The instrument bites.** Before the first route, :func:`instrument_control` loads a page
  with a known violation (an image with no alt text) and requires the same axe options to
  report ``image-alt``. A scanner that sees nothing there would see nothing anywhere.
* **The scan ran where it was sent.** After each scan the page's path is compared with the
  route's; a redirect (a login page, an error page) makes that route ``not_scanned`` with
  both URLs, never "clean".
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from urllib.parse import urlparse

from checks._browser import browser_context
from checks.a11y.axe import A11yReport, axe_source, scan
from checks.a11y.policy import A11yPolicy

CONTROL_HTML = '<!doctype html><html lang="en"><title>control</title><img src="data:,"></html>'
CONTROL_RULE = "image-alt"


class ScannerDoesNotBite(RuntimeError):
    """The scanner reported nothing on a page built to have a violation."""


def instrument_control(
    page: Any, policy: A11yPolicy | None = None, *, source: str | None = None
) -> A11yReport:
    """Scan a known-bad page; raise ``ScannerDoesNotBite`` unless ``image-alt`` is reported."""
    page.set_content(CONTROL_HTML)
    report = scan(page, policy, route="instrument-control", source=source)
    if CONTROL_RULE not in {v.rule_id for v in report.violations}:
        why = report.error or f"rules run: {report.rules_run}, reported: " + ", ".join(
            v.rule_id for v in report.violations
        )
        raise ScannerDoesNotBite(
            f"scanner does not bite: an image without alt text was not reported as "
            f"{CONTROL_RULE} ({why or 'nothing'})"
        )
    return report


def landed_elsewhere(requested: str, actual: str) -> bool:
    return (urlparse(requested).path or "/") != (urlparse(actual).path or "/")


def scan_routes(
    routes: Sequence[str],
    policy: A11yPolicy | None = None,
    browser_name: str = "chromium",
    *,
    channel: str | None = None,
    timeout_ms: int = 30_000,
    hosts: Sequence[str] = (),
) -> list[A11yReport]:
    """One report per route. A route that does not load is a report with an ``error``.

    Raises ``AxeUnavailable`` (no axe script) or ``BrowserUnavailable`` (no browser)
    before any navigation, so the caller can tell "could not scan" from "scanned", and
    ``ScannerDoesNotBite`` when the instrument control fails.
    """
    source = axe_source()
    reports: list[A11yReport] = []
    with browser_context(browser_name, channel=channel) as context:
        page = context.new_page()
        instrument_control(page, policy, source=source)
        for route in routes:
            try:
                response = page.goto(route, wait_until="load", timeout=timeout_ms)
            except Exception as exc:  # noqa: BLE001
                reports.append(A11yReport(route=route, error=f"unreachable: {exc}"))
                continue
            if response is not None and response.status >= 400:
                reports.append(A11yReport(route=route, error=f"answered {response.status}"))
                continue
            report = scan(page, policy, route=route, source=source, hosts=hosts)
            if landed_elsewhere(route, page.url):
                report = A11yReport(
                    route=route,
                    error=f"not_scanned: asked for {route}, the scan ran on {page.url}",
                )
            reports.append(report)
    return reports
