"""Ending a subscription through the screens: the precondition tick, the credit payee, three
ways into the flow for a company and an individual, and the chosen reason kept across a
validation error."""

from __future__ import annotations

import json

import pytest

from demo_sut.customers import make_iban
from tests.demo_examples import detector_pair
from tests.demo_examples.ui import support
from ui.pages.demo.draft_screen import DraftScreen
from ui.pages.demo.subscription_detail import SubscriptionDetail

pytestmark = pytest.mark.demo_ui

PRECONDITION_MESSAGE = "Tick the box to confirm the closure."
CLOSE_ON = "2026-03-16"


def open_closure_draft(page, base_url, sub_id) -> DraftScreen:
    detail = SubscriptionDetail(page, base_url).open(sub_id)
    started = detail.start("cancel")
    assert started.status == 201 and started.advanced, started.describe()
    draft = DraftScreen(page, base_url)
    draft.wait_ready()
    assert draft.kind() == "cancel" and draft.steps() == ["details", "credit"]
    return draft


def test_closure_is_blocked_until_the_precondition_is_ticked(
    demo_page, demo_sut, demo_control, demo_seed
):
    demo_control.freeze(support.NOW)
    sub = support.booked(demo_seed)
    draft = open_closure_draft(demo_page, demo_sut.base_url, sub["id"])
    draft.choose_reason("NO_LONGER_NEEDED")

    blocked = draft.confirm("confirm_cancel", timeout_ms=1500)
    assert not blocked.sent, "the confirm call left the browser although the box was unticked"
    assert draft.precondition_error() == PRECONDITION_MESSAGE
    assert draft.alerts_visible()

    assert draft.tick_precondition()
    done = draft.confirm("confirm_cancel")
    assert done.sent and done.outcome.status == 200 and done.outcome.advanced, (
        done.outcome.describe()
    )
    assert support.subscription(demo_sut.client, sub["id"])["status"] == "cancelled"


@pytest.mark.parametrize("debit", [False, True], ids=["payout-account", "debit-account"])
def test_the_credit_goes_to_the_first_complete_payee(
    demo_page, demo_sut, demo_control, demo_seed, debit
):
    """The payee is the first complete (account, holder) pair, in a fixed order of sources."""
    demo_control.freeze(support.NOW)
    customer = support.segment_customer(demo_seed, "company")
    fields = {}
    if debit:
        fields["payment_method"] = {
            "type": "DIRECT_DEBIT",
            "account": make_iban(bban="UIDEBIT0000041"),
        }
    sub = support.booked(demo_seed, customer=customer, **fields)

    draft = open_closure_draft(demo_page, demo_sut.base_url, sub["id"])
    draft.set_effective_date(support.date_of(CLOSE_ON))
    draft.choose_reason("BUDGET")
    draft.tick_precondition()
    result = draft.confirm("confirm_cancel")
    assert result.outcome.status == 200, result.outcome.describe()

    view = support.subscription(demo_sut.client, sub["id"], version=1)
    holder = view["holder"]["name"]
    candidates = [
        (
            "subscription",
            {"account": (view.get("payment_method") or {}).get("account"), "holder": holder},
        ),
        ("customer", {"account": customer["payout_account"], "holder": customer["display_name"]}),
    ]
    source, payee = support.first_complete(candidates)
    assert source == ("subscription" if debit else "customer")

    closure = support.latest(demo_sut.client, sub["id"])["closure"]
    assert closure["credit_total"] < 0, closure
    assert closure["payee_account"] == payee["account"], (
        f"credit paid to {closure['payee_account']}, expected the {source} account "
        f"{payee['account']} (candidates: {json.dumps(candidates)})"
    )


@pytest.mark.parametrize("route", ["fresh", "reuse", "deep-link"])
def test_company_closure_routes(demo_page, demo_sut, demo_control, demo_seed, route):
    """Three ways into the same closure; the confirm call is sent exactly once."""
    demo_control.freeze(support.NOW)
    client = demo_sut.client
    if route == "reuse":
        sub = support.reusable(client) or support.booked(demo_seed)
    else:
        sub = support.booked(demo_seed)
    if route == "deep-link":
        rest_draft = demo_seed.draft(sub["id"], "cancel")
        draft = DraftScreen(demo_page, demo_sut.base_url).open(rest_draft["id"])
    else:
        draft = open_closure_draft(demo_page, demo_sut.base_url, sub["id"])

    draft.set_effective_date(support.date_of(CLOSE_ON))
    draft.choose_reason("CONSOLIDATING")
    draft.tick_precondition()
    result = draft.confirm("confirm_cancel")
    assert result.sent and result.outcome.status == 200 and result.outcome.advanced, (
        result.outcome.describe()
    )

    detail = SubscriptionDetail(demo_page, demo_sut.base_url)
    detail.wait_loaded()
    assert detail.status() == "cancelled"
    rows = support.transactions(client, sub["id"], "CANCEL")
    assert len(rows) == 1, f"{len(rows)} closure transactions: the write was resent"
    assert rows[0]["period_start"] == CLOSE_ON and rows[0]["net"] < 0
    grid = [row for row in detail.ledger() if row["kind"] == "CANCEL"]
    assert len(grid) == 1 and grid[0]["net"] == support.money(rows[0]["net"])


@pytest.mark.parametrize("route", ["fresh", "reuse", "deep-link"])
def test_individual_closure_routes(demo_page, demo_sut, demo_control, demo_seed, route):
    """
    The individual variant. The fresh route first meets a draft left open earlier (409)
    and discards it through its own screen before trying again.
    """
    demo_control.freeze(support.NOW)
    client = demo_sut.client
    sub = (support.reusable(client) if route == "reuse" else None) or support.booked(
        demo_seed, "individual"
    )
    discarded = False
    if route == "deep-link":
        draft = DraftScreen(demo_page, demo_sut.base_url).open(
            demo_seed.draft(sub["id"], "cancel")["id"]
        )
    else:
        if route == "fresh":
            demo_seed.draft(sub["id"], "change")  # left open by an earlier session
        detail = SubscriptionDetail(demo_page, demo_sut.base_url).open(sub["id"])
        started, discarded = detail.start_discarding_a_stale_draft("cancel")
        assert started.status == 201 and started.advanced, started.describe()
        draft = DraftScreen(demo_page, demo_sut.base_url)
        draft.wait_ready()
    assert discarded is (route == "fresh")

    draft.set_effective_date(support.date_of(CLOSE_ON))
    draft.choose_reason("OTHER")
    draft.tick_precondition()
    result = draft.confirm("confirm_cancel")
    assert result.sent and result.outcome.status == 200, result.outcome.describe()
    assert len(support.transactions(client, sub["id"], "CANCEL")) == 1
    assert support.latest(client, sub["id"])["closure"]["reason"] == "OTHER"


def close_with(page, base_url, seed, sub, reason: str, *, error_first: bool):
    """Pick ``reason``; with ``error_first`` hit a validation error (no end date) before the
    real confirm. Returns the confirm result and the reason the screen held when it was sent."""
    draft = open_closure_draft(page, base_url, sub["id"])
    draft.choose_reason(reason)
    draft.tick_precondition()
    if error_first:
        assert draft.clear_effective_date() == ""
        blocked = draft.confirm("confirm_cancel", timeout_ms=1500)
        assert not blocked.sent and draft.field_error("effective_date"), "no validation error"
    draft.set_effective_date(support.date_of(CLOSE_ON))
    held = draft.chosen_reason()
    return draft.confirm("confirm_cancel"), held


@pytest.mark.parametrize("fault_on", [True, False], ids=["fault-on", "fault-off"])
def test_reason_survives_a_validation_error(demo_page, demo_sut, demo_control, demo_seed, fault_on):
    demo_control.freeze(support.NOW)
    demo_control.faults(*(["form_resets_select_after_error"] if fault_on else []))
    sub = support.booked(demo_seed)
    result, held = close_with(
        demo_page, demo_sut.base_url, demo_seed, sub, "CONSOLIDATING", error_first=True
    )

    def exercised() -> None:
        # without an error in between, the picked reason is what is sent, fault or not
        other = support.booked(demo_seed)
        clean, clean_held = close_with(
            demo_page, demo_sut.base_url, demo_seed, other, "BUDGET", error_first=False
        )
        assert clean.outcome.status == 200 and clean_held == "BUDGET", clean.outcome.describe()
        assert support.latest(demo_sut.client, other["id"])["closure"]["reason"] == "BUDGET"

    def oracle() -> None:
        assert held == "CONSOLIDATING", (
            f"DEMO-118: the chosen reason did not survive the error: the screen held {held!r}"
        )
        assert result.outcome.status == 200, result.outcome.describe()
        assert support.latest(demo_sut.client, sub["id"])["closure"]["reason"] == "CONSOLIDATING"

    detector_pair.judge(
        demo_control,
        ticket="DEMO-118",
        signature=r"DEMO-118: the chosen reason did not survive the error",
        oracle=oracle,
        exercised=exercised,
        fault="form_resets_select_after_error",
        fault_on=fault_on,
    )
