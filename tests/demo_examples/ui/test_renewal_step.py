"""The renewal draft's indexation step: navigation, the index chain, the new term end."""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

import pytest

from tests.demo_examples.ui import support
from ui.pages.demo.draft_screen import DraftScreen
from ui.pages.demo.subscription_detail import SubscriptionDetail
from verification.evidence_verdict import EvidenceVerdict

CENT = Decimal("0.01")


def one_calendar_year_later(day: date) -> date:
    """The anniversary; 29 February is the only date allowed to move (to 28 February)."""
    try:
        return day.replace(year=day.year + 1)
    except ValueError:
        return day.replace(year=day.year + 1, day=28)


def _renewable(demo_seed) -> dict:
    """A yearly subscription whose term ends within the renewal window, fully booked."""
    sub = support.segment_subscription(
        demo_seed, "company", start_date="2025-03-15", frequency="YEARLY"
    )
    assert demo_seed.book_due(sub["id"]), "nothing booked: the renewal precondition is not met"
    return sub


def _open_indexation(page, demo_sut, sub) -> DraftScreen:
    detail = SubscriptionDetail(page, demo_sut.base_url).open(sub["id"])
    assert "renew" in detail.actions()
    started = detail.start("renew")
    assert started.status == 201 and started.advanced, started.describe()
    draft = DraftScreen(page, demo_sut.base_url)
    draft.wait_ready()
    assert draft.kind() == "renew" and draft.renewal_step() == "indexation"
    return draft


@pytest.mark.demo_ui
def test_renewal_step_survives_back_forward(demo_page, demo_sut, demo_control, demo_seed):
    demo_control.freeze(support.NOW)
    draft = _open_indexation(demo_page, demo_sut, _renewable(demo_seed))
    draft.enter_index("3")
    before = draft.renewal_values()
    draft.renewal_next()
    assert draft.renewal_step() == "price"

    demo_page.go_back()
    draft.wait_for_renewal_preview()
    assert draft.renewal_step() == "indexation"
    after_back = draft.renewal_values()
    assert after_back == before, f"values changed across back: {before} -> {after_back}"
    assert draft.new_term_end_error() == ""

    demo_page.go_forward()
    draft.wait_for_renewal_preview()
    assert draft.renewal_step() == "price"
    result = draft.confirm("confirm_renew")
    assert result.outcome.status == 200 and result.outcome.advanced, result.outcome.describe()


@pytest.mark.demo_ui
def test_indexation_chain_on_screen_and_ledger(demo_page, demo_sut, demo_control, demo_seed):
    demo_control.freeze(support.NOW)
    sub = _renewable(demo_seed)
    draft = _open_indexation(demo_page, demo_sut, sub)
    base = draft.renewal_values()["current_price"]
    shown = draft.enter_index("1.2")

    expected = (base * Decimal("1.012")).quantize(CENT, ROUND_HALF_UP)
    bare = (base * Decimal("0.012")).quantize(CENT, ROUND_HALF_UP)
    assert shown != bare, f"new price {shown} = base x 0.012: the bare-multiplier defect"
    assert shown == expected, f"new price {shown}, expected base {base} x 1.012 = {expected}"

    draft.renewal_next()
    assert draft.confirm("confirm_renew").outcome.status == 200
    (renew,) = support.transactions(demo_sut.client, sub["id"], "RENEW")
    assert support.money(renew["net"]) == expected * 12, "yearly collection != 12 x new price"
    grid = [r for r in SubscriptionDetail(demo_page, demo_sut.base_url).open(sub["id"]).ledger()]
    assert [r["net"] for r in grid if r["kind"] == "RENEW"] == [expected * 12]


@pytest.mark.demo_ui
def test_term_end_extends_one_calendar_year(demo_page, demo_sut, demo_control, demo_seed):
    demo_control.freeze(support.NOW)
    verdict = EvidenceVerdict("renewal extends the term end by one calendar year")
    sub = _renewable(demo_seed)  # seeded here, so there is no empty pool to abstain on
    draft = _open_indexation(demo_page, demo_sut, sub)
    values = draft.renewal_values()
    old_end = date.fromisoformat(values["old_term_end"])
    new_end = date.fromisoformat(values["new_term_end"])
    assert old_end == date.fromisoformat(sub["term_end"])
    assert new_end == one_calendar_year_later(old_end), f"{old_end} -> {new_end}"
    assert draft.renewal_dates_read_only() == {"old": True, "new": True}

    draft.renewal_next()
    demo_page.go_back()
    draft.wait_for_renewal_preview()
    assert draft.renewal_values()["new_term_end"] == values["new_term_end"]
    demo_page.go_forward()
    draft.wait_for_renewal_preview()
    assert draft.confirm("confirm_renew").outcome.status == 200
    assert support.latest(demo_sut.client, sub["id"])["term_end"] == new_end.isoformat()
    verdict.proved(f"term end {old_end} -> {new_end}, read-only, kept across back/forward")


def test_one_calendar_year_oracle():
    """Offline: the oracle keeps the anniversary and clamps only 29 February."""
    assert one_calendar_year_later(date(2026, 3, 15)) == date(2027, 3, 15)
    assert one_calendar_year_later(date(2024, 2, 29)) == date(2025, 2, 28)
    assert one_calendar_year_later(date(2026, 12, 31)) == date(2027, 12, 31)
