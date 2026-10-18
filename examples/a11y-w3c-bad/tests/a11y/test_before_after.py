"""The demo's built-in control: the broken pages must fail, the fixed pages must not.

Every verdict on the "after" pages leans on these tests. If the "before" pages stop showing
their planted violations, the scanner (or the page it was handed) is wrong, and a clean
"after" scan proves nothing.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from a11y_w3c_bad.a11y import Scanner, ScanResult
from a11y_w3c_bad.a11y.pages import BEFORE, PAGES, PLANTED, PageState
from a11y_w3c_bad.a11y.scanner import table

pytestmark = pytest.mark.regression

#: "Materially fewer": the fixed page keeps at most a quarter of the blocking findings.
MATERIAL_REDUCTION = 0.25
DEMO_TITLE = re.compile("citylights", re.IGNORECASE)


def scan_demo(page: Page, scanner: Scanner, state: PageState, artifact_dir: Path) -> ScanResult:
    response = page.goto(state.path)
    assert response is not None
    assert response.ok, f"{state.path} answered {response.status}"
    # The instrument check: a challenge or error page must never be scanned as the demo.
    expect(page).to_have_title(DEMO_TITLE)
    result = scanner.scan()
    target = artifact_dir / f"axe-{state.path.replace('/', '-')}.json"
    target.write_text(json.dumps(result.to_json(), indent=2), encoding="utf-8")
    return result


@pytest.mark.smoke
@pytest.mark.parametrize("state", BEFORE, ids=lambda state: state.id)
def test_before_page_shows_its_planted_violations(page, scanner, state, artifact_dir):
    result = scan_demo(page, scanner, state, artifact_dir)
    missing = PLANTED[state.id] - {finding.rule for finding in result.blocking}
    assert not missing, (
        f"the scanner no longer finds {sorted(missing)} on before/{state.id}: the control is "
        f"broken, so no 'after' verdict can be trusted\n{table(result.findings)}"
    )


@pytest.mark.parametrize(
    ("before", "after"), list(zip(BEFORE, PAGES, strict=True)), ids=[s.id for s in PAGES]
)
def test_after_page_has_materially_fewer_violations(page, scanner, before, after, artifact_dir):
    broken = scan_demo(page, scanner, before, artifact_dir)
    fixed = scan_demo(page, scanner, after, artifact_dir)

    assert len(fixed.blocking) <= MATERIAL_REDUCTION * len(broken.blocking), table(fixed.blocking)
    assert not PLANTED[after.id] & {finding.rule for finding in fixed.blocking}
