"""A tester runs a manual case in a real browser, and the run page states what it proved.

Sign in with a token, start a manual run from spec markdown, record each step (one with a
failure), finish the case and the run, then read the run page: the integrity verdict, the
red result, and a pass count that is not presented as proof.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.demo_ui

SPEC = """## Scenario Checkout with a voucher
### Steps
1. Add an item to the basket
2. Apply the voucher
3. Pay
### Expected
- The voucher discount is applied
### Tags
REQ-21
"""


def _tid(name: str) -> str:
    return f'[data-testid="{name}"]'


def test_manual_run_in_the_browser(page, platform):
    base, token = platform
    page.goto(f"{base}/ui/login")
    page.fill(_tid("login-token"), token)
    page.click(_tid("login-submit"))
    page.goto(f"{base}/ui/default/manual")
    page.fill(_tid("manual-name"), "voucher check")
    page.fill(_tid("manual-spec"), SPEC)
    page.click(_tid("manual-start"))
    page.click(_tid("execution-link"))
    results = [("pass", "added"), ("fail", "voucher rejected"), ("blocked", "cannot pay")]
    for n, (status, actual) in enumerate(results):
        step = page.locator(_tid("step")).nth(n)
        step.locator(_tid("step-status")).select_option(status)
        step.locator(_tid("step-actual")).fill(actual)
        step.locator(_tid("step-save")).click()
    page.click(_tid("finish-execution"))
    assert page.locator(_tid("execution-status")).inner_text() == "failed"
    page.click(_tid("finish-run"))
    verdict = page.locator(_tid("integrity")).inner_text()
    assert "Run trust: PASS" in verdict and "1 red of 1 executed" in verdict
    counts = page.locator(_tid("counts")).inner_text()
    assert "proved" in counts and "not exercised" in counts
    assert "step 2 failed: voucher rejected" in page.content()


def test_signed_out_visitor_is_sent_to_sign_in(page, platform):
    base, _ = platform
    page.goto(f"{base}/ui/default/runs")
    assert page.url.endswith("/ui/login")
    assert page.locator(_tid("login-token")).is_visible()


def test_exploratory_session_with_a_real_browser_capture(page, browser, platform, tmp_path):
    """A tester opens a session in the UI, explores with a captured browser context, and
    the trace and the browser's errors land on the session as notes."""
    from qaplatform.capture import open_capture
    from qaplatform.client import PlatformClient

    base, token = platform
    page.goto(f"{base}/ui/login")
    page.fill(_tid("login-token"), token)
    page.click(_tid("login-submit"))
    page.goto(f"{base}/ui/default/sessions")
    page.fill(_tid("session-charter"), "Explore the run list with filters")
    page.fill(_tid("session-requirements"), "REQ-5")
    page.click(_tid("session-start"))
    session_id = page.url.rsplit("/", 1)[1]

    explored = browser.new_context()
    client = PlatformClient(base, token, "default", timeout_s=10)
    with open_capture(client, session_id, explored, artifact_dir=tmp_path):
        probe = explored.new_page()
        probe.goto(f"{base}/ui/login")
        probe.evaluate("console.error('search box ignores Enter')")
    assert (tmp_path / f"session-{session_id}.zip").stat().st_size > 0
    explored.close()

    page.reload()
    notes = page.locator(_tid("notes")).inner_text()
    assert "console error: search box ignores Enter" in notes
    assert f"session-{session_id}.zip" in notes
    page.fill(_tid("session-debrief"), "filters covered; sorting not")
    page.click(_tid("session-end"))
    assert page.locator(_tid("session-status")).inner_text() == "ended"
