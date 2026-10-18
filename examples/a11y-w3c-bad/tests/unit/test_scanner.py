"""The scanner against local HTML with planted violations: needs Chromium, never the network.

This is the positive control for every live result. If these fail, a clean live scan means
nothing: the scanner did not run, or ran without the rules it claims to.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import Page

from a11y_w3c_bad.a11y import Scanner
from a11y_w3c_bad.a11y.scanner import bundled_axe

pytestmark = pytest.mark.offline

STATIC = Path(__file__).parents[1] / "fixtures" / "static"
#: One element per rule in before.html, each fixed in after.html.
PLANTED = {"image-alt", "color-contrast", "label", "button-name", "html-has-lang"}


@pytest.fixture
def before(page: Page) -> Page:
    page.set_content((STATIC / "before.html").read_text(encoding="utf-8"))
    return page


def test_the_scanner_finds_every_planted_violation(before: Page):
    result = Scanner(before).scan()
    assert result.rules >= PLANTED, f"missed: {sorted(PLANTED - result.rules)}"
    assert {f.impact for f in result.findings} <= {"critical", "serious", "moderate", "minor"}


def test_the_planted_violations_are_blocking(before: Page):
    assert {f.rule for f in Scanner(before).scan().blocking} >= PLANTED


def test_the_fixed_page_has_no_blocking_violations(page: Page):
    page.set_content((STATIC / "after.html").read_text(encoding="utf-8"))
    assert Scanner(page).scan().blocking == ()


def test_only_the_configured_tags_run(before: Page):
    """``empty-heading`` is a best-practice rule: out of a WCAG scan, in when asked for."""
    assert "empty-heading" not in Scanner(before).scan().rules
    assert "empty-heading" in Scanner(before, tags=["best-practice"]).scan().rules


def test_a_scan_can_be_limited_to_one_part_of_the_page(before: Page):
    result = Scanner(before).scan(include="#logo")
    assert result.rules == {"image-alt"}


def test_a_pinned_script_replaces_the_bundled_build(before: Page, tmp_path):
    """The fallback path: inject a pinned axe.min.js from a file instead of the package's."""
    pinned = tmp_path / "axe.min.js"
    pinned.write_text(bundled_axe(), encoding="utf-8")

    result = Scanner(before, script=pinned.read_text(encoding="utf-8")).scan()

    assert result.rules >= PLANTED
