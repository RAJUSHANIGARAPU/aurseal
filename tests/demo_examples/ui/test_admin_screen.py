"""A retired admin entry: gone from the cards, its page still reachable by URL."""

from __future__ import annotations

import pytest

from ui.pages.demo.admin import AdminOverview
from verification.evidence_verdict import EvidenceVerdict

pytestmark = pytest.mark.demo_ui

RETIRED = "Archived lists"
CONTROLS = ("Ledger accounts", "Plans", "Partners")


def test_retired_entry_absent_with_control(demo_page, demo_sut, demo_control):
    verdict = EvidenceVerdict("admin cards no longer offer the retired entry")
    admin = AdminOverview(demo_page, demo_sut.base_url).open()
    links = admin.links()
    # positive control first: the card that used to carry the entry still renders its siblings
    for control in CONTROLS:
        assert control in links, f"control entry {control!r} missing; absence would prove nothing"
    assert admin.link_count("archived-lists") == 0
    assert RETIRED not in links
    verdict.proved(f"links {links} contain the controls {list(CONTROLS)} and not {RETIRED!r}")


def test_retired_page_still_reachable_by_url(demo_page, demo_sut, demo_control):
    page = AdminOverview(demo_page, demo_sut.base_url)
    response = demo_page.goto(demo_sut.base_url + "/ui/admin/archived-lists")
    assert response.status == 200
    page.resolve("page.breadcrumb").wait_for()
    assert page.breadcrumb() == ["Admin", RETIRED]
    assert page.title() == RETIRED
    EvidenceVerdict("retired page reachable").proved(
        f"HTTP {response.status}, breadcrumb {page.breadcrumb()}"
    )
