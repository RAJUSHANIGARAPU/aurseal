"""Live scans of the target, page state by page state, judged against the baseline."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from playwright.sync_api import Page

from a11y_w3c_bad.a11y import Scanner, ScanResult, judge, load_baseline
from a11y_w3c_bad.a11y.pages import PAGES, PageState
from a11y_w3c_bad.a11y.scanner import table

pytestmark = pytest.mark.regression

log = logging.getLogger(__name__)
BASELINE = load_baseline()


def scan(page: Page, scanner: Scanner, state: PageState, artifact_dir: Path) -> ScanResult:
    response = page.goto(state.path)
    assert response is not None, f"no response for {state.path}"
    assert response.ok, (
        f"{state.path} answered {response.status}: the scan would judge an error page"
    )
    state.prepare(page)
    result = scanner.scan()
    (artifact_dir / "axe.json").write_text(json.dumps(result.to_json(), indent=2), encoding="utf-8")
    reported = [finding for finding in result.findings if not finding.blocking]
    if reported:
        log.warning("%s: moderate and minor findings\n%s", state.id, table(reported))
    return result


@pytest.mark.smoke
def test_scanner_detects_a_planted_violation_on_the_target(page: Page, scanner: Scanner):
    """Positive control on the live page: proves axe runs here before a clean scan counts."""
    first = PAGES[0]
    response = page.goto(first.path)
    assert response is not None
    assert response.ok, f"{first.path} answered {response.status}"
    page.evaluate(
        """() => {
            const img = document.createElement("img");
            img.id = "a11y-control";
            img.src = "data:image/gif;base64,R0lGODlhAQABAAAAACw=";
            document.body.append(img);
        }"""
    )
    assert "image-alt" in scanner.scan(include="#a11y-control").rules


@pytest.mark.parametrize("state", PAGES, ids=lambda state: state.id)
def test_page_has_no_new_serious_violations(page, scanner, state, artifact_dir):
    verdict = judge(state.id, scan(page, scanner, state, artifact_dir), BASELINE)
    assert verdict.new == (), (
        f"{len(verdict.new)} new serious or critical violation(s) on {state.id}; fix them, or "
        f"file each one and add it to a11y-baseline.json\n{table(verdict.new)}"
    )


@pytest.mark.parametrize("state", PAGES, ids=lambda state: state.id)
def test_baseline_entries_still_reproduce(page, scanner, state, artifact_dir):
    verdict = judge(state.id, scan(page, scanner, state, artifact_dir), BASELINE)
    cleared = "\n".join(f"- {e.rule} on `{e.target}` ({e.issue})" for e in verdict.cleared)
    assert verdict.cleared == (), (
        f"fixed, or the page changed: remove these entries from a11y-baseline.json\n{cleared}"
    )
