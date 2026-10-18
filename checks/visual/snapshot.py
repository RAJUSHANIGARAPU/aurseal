"""Capture a page, compare it with its baseline, keep the evidence, return a verdict.

Evidence goes to ``reports/visual/<run_id>/``: ``<name>.actual.png`` always, and
``<name>.diff.png`` (actual faded to grey, differing pixels red) when pixels differ. The
approve command reads the actuals from there.

Outcomes: match ⇒ ``pass``; mismatch or size change ⇒ ``fail``; missing baseline ⇒
``unknown`` ("nothing compared"), or, when updating, the baseline is written and the
outcome is a ``skip`` with ``baseline_taken`` — never a ``pass``.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from checks._outcome import CheckOutcome
from checks._sink import REPO_ROOT, MetricSink, metric_row
from checks.visual.baselines import BaselineStore, check_name, sha256
from checks.visual.compare import MISSING_BASELINE, VisualDiff, compare

KIND = "visual"
TABLE = "metric_visual_diff"
UPDATE_ENV = "VISUAL_UPDATE"
REPORT_ROOT = REPO_ROOT / "reports" / "visual"


def update_requested(explicit: bool | None = None) -> bool:
    if explicit is not None:
        return explicit
    return os.environ.get(UPDATE_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def report_dir(run_id: str | None = None) -> Path:
    if run_id is None:
        from core.run_id import run_id as current

        run_id = current()
    return REPORT_ROOT / run_id


def capture(page: Any, *, mask: Sequence[Any] = (), full_page: bool = False) -> bytes:
    """A deterministic screenshot: fonts loaded, animations stopped, caret hidden."""
    page.evaluate("() => document.fonts ? document.fonts.ready.then(() => true) : true")
    return page.screenshot(
        full_page=full_page, animations="disabled", caret="hide", mask=list(mask)
    )


def check_snapshot(
    actual: bytes,
    name: str,
    store: BaselineStore,
    *,
    out_dir: Path | None = None,
    update: bool | None = None,
    pixel_threshold: float = 0.1,
    max_diff_ratio: float = 0.001,
    sink: MetricSink | None = None,
    nodeid: str | None = None,
) -> tuple[CheckOutcome, VisualDiff]:
    check_name(name)
    out_dir = out_dir or report_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    actual_path = out_dir / f"{name}.actual.png"
    actual_path.write_bytes(actual)
    diff = compare(
        store.load(name), actual, pixel_threshold=pixel_threshold, max_diff_ratio=max_diff_ratio
    )
    diff_path = None
    if diff.diff_png:
        diff_path = out_dir / f"{name}.diff.png"
        diff_path.write_bytes(diff.diff_png)
    evidence = [p for p in (actual_path, diff_path) if p is not None]
    metrics = {
        "name": name,
        "browser": store.browser,
        "platform": store.platform,
        "diff_pixels": diff.diff_pixels,
        "diff_ratio": diff.diff_ratio,
        "outcome": diff.outcome,
    }
    if diff.outcome == MISSING_BASELINE:
        if update_requested(update):
            path = store.write(name, actual)
            outcome = CheckOutcome.skipped(
                KIND,
                f"baseline taken for {name} at {path}; review and commit it",
                baseline_taken=True,
                metrics=metrics,
                evidence=[path, actual_path],
            )
        else:
            outcome = CheckOutcome.unknown(
                KIND,
                f"no baseline for {name} ({store.path(name)}): nothing compared",
                metrics=metrics,
                evidence=evidence,
            )
    elif diff.matched:
        outcome = CheckOutcome.ok(
            KIND, f"{name}: {diff.detail}", metrics=metrics, evidence=evidence
        )
    else:
        outcome = CheckOutcome.breached(
            KIND, f"{name}: {diff.outcome}: {diff.detail}", metrics=metrics, evidence=evidence
        )
    if sink is not None:
        baseline = store.load(name)
        sink.write(
            TABLE,
            [
                metric_row(
                    nodeid=nodeid,
                    name=name,
                    browser=store.browser,
                    width=diff.width,
                    height=diff.height,
                    diff_pixels=diff.diff_pixels,
                    diff_ratio=diff.diff_ratio,
                    threshold=max_diff_ratio,
                    outcome=diff.outcome,
                    baseline_sha256=sha256(baseline) if baseline else None,
                    actual_path=str(actual_path),
                    diff_path=str(diff_path) if diff_path else None,
                )
            ],
        )
    return outcome, diff
