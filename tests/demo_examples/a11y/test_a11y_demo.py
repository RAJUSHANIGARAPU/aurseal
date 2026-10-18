"""Accessibility scans with axe-core in a real browser, against fixture pages and the demo.

* Positive controls: a known-bad page must report ``image-alt`` and ``label``; a known-good
  page must be clean with rules actually run. Without them "zero violations" could mean
  "axe never ran".
* The demo's key screens are scanned with the default policy (WCAG 2 A/AA, ``serious``+).
* A detector pair on ``/ui/subscriptions`` with the demo fault ``screen_filter_unlabelled``:
  on, the unlabelled filter reproduces; off, the detector clears.
* The open-issue manifest scoped to route + rule: with the fault on the known violation
  is excused and asserted; with it off the entry reports ``KNOWN DEFECT CLEARED``.

Needs ``axe-playwright-python`` (requirements-checks.txt); skips cleanly without it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from checks._sink import MemorySink
from checks.a11y.assertions import assert_accessible
from checks.a11y.axe import scan
from checks.a11y.judge import judge, record
from checks.a11y.policy import A11yPolicy
from tests.checks._require import require_module
from tests.demo_examples import detector_pair
from verification.known_defects_manifest import check_manifest, load_manifest
from verification.verdicts import VERDICTS_FILE_ENV, verdicts_path

pytestmark = pytest.mark.demo_ui

MANIFEST = load_manifest(Path(__file__).with_name("a11y.manifest.json"))
FAULT = "screen_filter_unlabelled"
KEY_PAGES = ["/login", "/ui", "/ui/customers", "/ui/customers/new", "/ui/admin", "/ui/tasks"]

BAD_PAGE = """<!doctype html><html lang="en"><head><title>Bad</title></head><body><main>
<h1>Known bad</h1><img src="data:image/gif;base64,R0lGODlhAQABAAAAACw=">
<input type="text" id="q"></main></body></html>"""
GOOD_PAGE = """<!doctype html><html lang="en"><head><title>Good</title></head><body><main>
<h1>Known good</h1><img alt="A square" src="data:image/gif;base64,R0lGODlhAQABAAAAACw=">
<label for="q">Search</label><input type="text" id="q"></main></body></html>"""


@pytest.fixture(autouse=True)
def _axe() -> None:
    require_module("axe_playwright_python")


@pytest.fixture
def own_verdicts(tmp_path, monkeypatch):
    """Record this test's verdicts in its own file, then append them to the run's file.

    The two sides of a pair share a ticket and may run at the same moment on two xdist
    workers; reading "the last verdict for the ticket" from the shared file could then
    read the other side's. The verdicts still reach the run's file afterwards.
    """
    shared = verdicts_path()
    local = tmp_path / "verdicts.jsonl"
    monkeypatch.setenv(VERDICTS_FILE_ENV, str(local))
    yield
    if local.is_file():
        shared.parent.mkdir(parents=True, exist_ok=True)
        with shared.open("ab") as handle:
            handle.write(local.read_bytes())


def test_the_known_bad_page_reports_its_violations(page):
    page.set_content(BAD_PAGE)
    report = scan(page, route="fixture:bad")
    assert report.scanned and report.rules_run > 10
    assert {"image-alt", "label"} <= {v.rule_id for v in report.violations}
    assert judge([report], record_verdicts=False).status == "fail"


def test_the_known_good_page_is_clean(page):
    page.set_content(GOOD_PAGE)
    report = scan(page, route="fixture:good")
    assert report.scanned and report.rules_run > 10
    assert report.violations == []
    assert judge([report], record_verdicts=False).status == "pass"


@pytest.mark.parametrize("path", KEY_PAGES)
def test_demo_key_page_is_accessible(page, demo_sut, demo_control, path):
    demo_control.faults()
    sink = MemorySink()
    page.goto(demo_sut.base_url + path)
    report = assert_accessible(page, A11yPolicy(), manifest=MANIFEST, sink=sink)
    assert report.rules_run > 10
    assert sink.rows["metric_a11y_scan"][0]["route"] == path


def _scan_list(page, demo_sut):
    page.goto(demo_sut.base_url + "/ui/subscriptions")
    page.locator('[data-testid="filter-input"]').wait_for()
    return scan(page)


@pytest.mark.parametrize("fault_on", [True, False], ids=["fault-on", "fault-off"])
def test_unlabelled_filter_detector(page, demo_sut, demo_control, own_verdicts, fault_on):
    if fault_on:
        demo_control.fault(FAULT)
    else:
        demo_control.faults()
    reports = []

    def exercised() -> None:
        reports.append(_scan_list(page, demo_sut))
        assert reports[-1].scanned and "label" in reports[-1].rule_ids, "axe did not run 'label'"

    def oracle() -> None:
        outcome = judge([reports[-1]], record_verdicts=False)
        assert outcome.status == "pass", outcome.reason

    detector_pair.judge(
        demo_control,
        ticket="BUG-171",
        signature=r"/ui/subscriptions: blocking violations \['label'\]",
        oracle=oracle,
        exercised=exercised,
        fault=FAULT,
        fault_on=fault_on,
    )


def test_known_defect_is_scoped_to_its_route_and_rule(page, demo_sut, demo_control):
    assert check_manifest(MANIFEST, emitted_kinds={"label"}) == []
    demo_control.fault(FAULT)
    report = _scan_list(page, demo_sut)
    sink = MemorySink()
    record([report], sink, MANIFEST)
    out = judge([report], manifest=MANIFEST, record_verdicts=False)
    assert out.status == "pass", out.reason
    assert out.findings[0]["open_issues"] == [{"issue": "BUG-171", "verdict": "reproduces"}]
    assert sink.rows["metric_a11y_violation"][0]["known_defect"] == "BUG-171"
    assert demo_control.evidence() == FAULT


def test_known_defect_reports_cleared_once_fixed(page, demo_sut, demo_control):
    demo_control.faults()
    out = judge([_scan_list(page, demo_sut)], manifest=MANIFEST, record_verdicts=False)
    assert out.status == "fail"
    assert "KNOWN DEFECT CLEARED BUG-171" in out.reason
    assert demo_control.evidence() == "none"
