"""Capture and compare named routes in a browser this module starts (plugin use)."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from checks._browser import browser_context
from checks._outcome import CheckOutcome
from checks._sink import MetricSink
from checks.visual.baselines import BaselineStore
from checks.visual.snapshot import KIND, capture, check_snapshot


def snapshot_routes(
    routes: Mapping[str, str],
    store: BaselineStore,
    *,
    out_dir: Path | None = None,
    update: bool = False,
    pixel_threshold: float = 0.1,
    max_diff_ratio: float = 0.001,
    sink: MetricSink | None = None,
    timeout_ms: int = 30_000,
) -> list[CheckOutcome]:
    """One outcome per ``{name: url}``. A route that does not load is ``unknown``.

    Raises ``BrowserUnavailable`` before any navigation when no browser starts, and
    ``PillowUnavailable`` from the first comparison when Pillow is absent.
    """
    outcomes: list[CheckOutcome] = []
    with browser_context(store.browser) as context:
        page = context.new_page()
        for name, url in routes.items():
            try:
                response = page.goto(url, wait_until="load", timeout=timeout_ms)
            except Exception as exc:  # noqa: BLE001
                outcomes.append(CheckOutcome.unknown(KIND, f"{name}: unreachable: {exc}"))
                continue
            if response is not None and response.status >= 400:
                outcomes.append(CheckOutcome.unknown(KIND, f"{name}: answered {response.status}"))
                continue
            outcome, _ = check_snapshot(
                capture(page),
                name,
                store,
                out_dir=out_dir,
                update=update,
                pixel_threshold=pixel_threshold,
                max_diff_ratio=max_diff_ratio,
                sink=sink,
            )
            outcomes.append(outcome)
    return outcomes


def combine(outcomes: list[CheckOutcome]) -> CheckOutcome:
    """One verdict for many snapshots: any mismatch fails; anything uncompared is unknown."""
    if not outcomes:
        return CheckOutcome.unknown(KIND, "no routes were compared")
    findings = [o.summary() for o in outcomes]
    failed = [o for o in outcomes if o.status == "fail"]
    if failed:
        return CheckOutcome.breached(KIND, "; ".join(o.reason for o in failed), findings=findings)
    unknown = [o for o in outcomes if o.status in ("unknown", "error")]
    if unknown:
        return CheckOutcome.unknown(KIND, "; ".join(o.reason for o in unknown), findings=findings)
    taken = [o for o in outcomes if o.baseline_taken]
    if taken:
        return CheckOutcome.skipped(
            KIND,
            f"{len(taken)} baseline(s) taken; nothing judged on those",
            baseline_taken=True,
            findings=findings,
        )
    return CheckOutcome.ok(KIND, f"{len(outcomes)} snapshot(s) match", findings=findings)
