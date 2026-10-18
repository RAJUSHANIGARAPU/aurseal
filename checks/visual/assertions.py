"""``assert_matches_baseline(page, name)`` for use inside any UI test.

* match ⇒ returns the diff;
* mismatch or size change ⇒ ``AssertionError`` naming the diff image;
* missing baseline ⇒ ``AssertionError`` ("no baseline: nothing compared"), unless updating
  (``--visual-update`` with ``checks.visual.pytest_plugin`` loaded, or ``VISUAL_UPDATE=1``):
  then the baseline is written and the test is skipped as ``baseline taken``. Never a pass.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest

from checks.visual.baselines import BaselineStore
from checks.visual.compare import VisualDiff
from checks.visual.snapshot import capture, check_snapshot


def assert_matches_baseline(
    page: Any,
    name: str,
    *,
    store: BaselineStore | None = None,
    browser: str = "chromium",
    mask: Sequence[Any] = (),
    full_page: bool = False,
    update: bool | None = None,
    pixel_threshold: float = 0.1,
    max_diff_ratio: float = 0.001,
    **kwargs: Any,
) -> VisualDiff:
    store = store or BaselineStore(browser=browser)
    actual = capture(page, mask=mask, full_page=full_page)
    outcome, diff = check_snapshot(
        actual,
        name,
        store,
        update=update,
        pixel_threshold=pixel_threshold,
        max_diff_ratio=max_diff_ratio,
        **kwargs,
    )
    if outcome.baseline_taken:
        pytest.skip(f"baseline_taken: {outcome.reason}")
    if outcome.status != "pass":
        evidence = ", ".join(str(p) for p in outcome.evidence)
        raise AssertionError(f"visual {outcome.status}: {outcome.reason} [evidence: {evidence}]")
    return diff
