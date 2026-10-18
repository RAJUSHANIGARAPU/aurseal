"""Visual regression in a real browser: fixture controls and the demo's stable screens.

* Positive control without committed files: a page is captured as its own baseline (in a
  temporary store); unchanged it matches, with one element changed it mismatches and a red
  diff image is written. Without this, "match" could mean "nothing was compared".
* The demo's stable screens are compared with committed baselines in
  ``visual_baselines/<browser>-<platform>/``. A missing baseline fails the test; take it
  with ``VISUAL_UPDATE=1`` (or ``--visual-update`` with ``-p checks.visual.pytest_plugin``),
  which skips the test as "baseline taken", then review and commit the PNG.
* A fault switched on in the demo changes every screen's fault banner, so the same screen
  must now mismatch its baseline: the committed baseline is genuinely compared.

Needs Pillow (requirements-checks.txt); skips cleanly without it.
"""

from __future__ import annotations

import pytest

from checks._sink import MemorySink
from checks.visual.assertions import assert_matches_baseline
from checks.visual.baselines import BaselineStore
from checks.visual.snapshot import capture, check_snapshot
from tests.checks._require import require_module

pytestmark = pytest.mark.demo_ui

STABLE_SCREENS = {"demo-login": "/login", "demo-customer-form": "/ui/customers/new"}
PAGE = """<!doctype html><html lang="en"><body style="margin:0;font:16px sans-serif">
<main style="padding:24px"><h1 id="title">Order summary</h1>
<p id="total" style="background:#e8f0ff;padding:8px;width:240px">Total: 42.00</p>
</main></body></html>"""


@pytest.fixture(autouse=True)
def _pillow() -> None:
    require_module("PIL.Image")


@pytest.fixture
def store(browser_name) -> BaselineStore:
    return BaselineStore(browser=browser_name)


def test_an_unchanged_page_matches_its_baseline(page, tmp_path):
    page.set_content(PAGE)
    temp = BaselineStore(tmp_path / "base", "chromium", "control")
    first, _ = check_snapshot(capture(page), "control", temp, out_dir=tmp_path, update=True)
    assert first.baseline_taken and first.status == "skip"
    second, diff = check_snapshot(capture(page), "control", temp, out_dir=tmp_path)
    assert second.status == "pass" and diff.diff_pixels == 0


def test_a_changed_element_mismatches_its_baseline(page, tmp_path):
    """The box's background changes colour: thousands of pixels, far above the 0.1 % limit."""
    page.set_content(PAGE)
    temp = BaselineStore(tmp_path / "base", "chromium", "control")
    temp.write("control", capture(page))
    page.evaluate("() => { document.getElementById('total').style.background = '#ffd27a'; }")
    sink = MemorySink()
    outcome, diff = check_snapshot(capture(page), "control", temp, out_dir=tmp_path, sink=sink)
    assert outcome.status == "fail" and diff.outcome == "mismatch"
    assert diff.diff_pixels > 0
    assert (tmp_path / "control.diff.png").is_file()
    assert sink.rows["metric_visual_diff"][0]["outcome"] == "mismatch"


@pytest.mark.parametrize("name", sorted(STABLE_SCREENS))
def test_demo_screen_matches_its_committed_baseline(page, demo_sut, demo_control, store, name):
    demo_control.faults()
    page.goto(demo_sut.base_url + STABLE_SCREENS[name])
    diff = assert_matches_baseline(page, name, store=store)
    assert diff.matched


def test_a_fault_banner_change_is_caught(page, demo_sut, demo_control, store, tmp_path):
    baseline = store.load("demo-login")
    if baseline is None:
        pytest.fail(f"no committed baseline {store.path('demo-login')}: nothing to compare")
    demo_control.fault("rounding_drift")
    page.goto(demo_sut.base_url + STABLE_SCREENS["demo-login"])
    outcome, diff = check_snapshot(capture(page), "demo-login", store, out_dir=tmp_path)
    assert demo_control.evidence() == "rounding_drift"
    assert outcome.status == "fail", outcome.reason
    assert diff.diff_pixels > 0
