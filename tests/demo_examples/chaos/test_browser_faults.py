"""Browser-side fault injection: the demo's screens under a failing API (demo_ui lane).

The subscription list loads its rows with ``POST /api/search``. With that call failing, the
screen must show its error banner and must not go blank; with the fault removed, a reload
must bring the list back with no banner. Routing-level injection tests the client's
handling of failure, not the server's.
"""

from __future__ import annotations

import pytest

from checks.faults.oracles import error_is_shown, no_blank_page, recovers_after, retries_bounded
from checks.faults.spec import FaultSpec

pytestmark = [pytest.mark.demo_ui, pytest.mark.chaos]

SEARCH = "**/api/search"


def _open_list(page, demo_sut) -> None:
    page.goto(demo_sut.base_url + "/ui/subscriptions")
    page.wait_for_load_state("networkidle")


@pytest.mark.parametrize(
    "spec",
    [
        FaultSpec("status", SEARCH, method="POST", status=503),
        FaultSpec("abort", SEARCH, method="POST"),
    ],
    ids=["status-503", "connection-reset"],
)
def test_the_list_shows_an_error_and_recovers(
    page, demo_sut, demo_control, demo_seed, faults, spec
):
    demo_control.faults()
    demo_seed.subscription()
    handle = faults.browser(page, [spec])
    _open_list(page, demo_sut)
    banner = page.get_by_test_id("error-banner")
    faults.note(error_is_shown(banner)).check()
    faults.note(no_blank_page(page)).check()
    faults.note(retries_bounded(handle, 2)).check()
    assert "Request failed" in banner.inner_text()

    def list_is_back() -> bool:
        page.get_by_test_id("result-count").wait_for()
        rows = page.locator('[data-testid="subscriptions-table"] tbody tr')
        rows.first.wait_for()
        return banner.is_hidden() and rows.count() > 0

    result = recovers_after(handle, lambda: _open_list(page, demo_sut), list_is_back)
    faults.note(result).check()


def test_a_slow_api_still_renders_the_list(page, demo_sut, demo_control, demo_seed, faults):
    demo_control.faults()
    demo_seed.subscription()
    faults.browser(page, [FaultSpec("latency", SEARCH, method="POST", latency_ms=600)])
    _open_list(page, demo_sut)
    page.locator('[data-testid="subscriptions-table"] tbody tr').first.wait_for()
    assert page.get_by_test_id("error-banner").is_hidden()
