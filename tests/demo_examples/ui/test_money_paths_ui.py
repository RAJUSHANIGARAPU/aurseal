"""Money-path drivers: a partial credit, a pro-rata price delta and a payment-method change.

Each driver runs the money-moving flow on screen and checks the amount it booked
against an independent day-count oracle, on the grid and in the API.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from demo_sut.customers import make_iban
from oracles import daycount
from tests.demo_examples.ui import support
from ui.pages.demo.draft_screen import DraftScreen
from ui.pages.demo.subscription_detail import SubscriptionDetail

pytestmark = pytest.mark.demo_ui

MARCH = (support.date_of("2026-03-01"), support.date_of("2026-04-01"))
EFFECTIVE = support.date_of("2026-03-16")


def _grid_amount(page, base_url, sub_id, kind: str) -> Decimal:
    rows = [
        r for r in SubscriptionDetail(page, base_url).open(sub_id).ledger() if r["kind"] == kind
    ]
    assert len(rows) == 1, f"{len(rows)} {kind} rows on the grid"
    return rows[0]["net"]


def _drive_partial_credit(page, demo_sut, demo_seed) -> str:
    sub = support.booked(demo_seed)
    draft = DraftScreen(page, demo_sut.base_url).open(demo_seed.draft(sub["id"], "cancel")["id"])
    draft.set_effective_date(EFFECTIVE)
    draft.choose_reason("BUDGET")
    draft.tick_precondition()
    assert draft.confirm("confirm_cancel").outcome.status == 200

    period = daycount.Period(*MARCH, termination=EFFECTIVE, end_inclusive=False)
    full = support.money(sub["price"]["period_net"])  # one booked month, net
    expected = daycount.candidate_refund(period, full, daycount.DayCount.ACT_ACT)
    (cancel,) = support.transactions(demo_sut.client, sub["id"], "CANCEL")
    observed = support.money(cancel["net"])
    assert daycount.within_pro_rata_bounds(observed, full), observed
    assert observed == expected, daycount.describe_pro_rata(period, full, observed)
    assert _grid_amount(page, demo_sut.base_url, sub["id"], "CANCEL") == expected
    return f"credit {observed} = {daycount.describe_match(period, full, observed)}"


def _drive_price_delta(page, demo_sut, demo_seed) -> str:
    sub = support.booked(demo_seed)
    raise_by = Decimal("2.80")
    draft = DraftScreen(page, demo_sut.base_url).open(demo_seed.draft(sub["id"], "change")["id"])
    draft.set_effective_date(EFFECTIVE)
    draft.set_monthly_net(str(support.money(sub["price"]["monthly_net"]) + raise_by))
    assert draft.confirm("confirm_change").outcome.status == 200

    period = daycount.Period(*MARCH, termination=EFFECTIVE, end_inclusive=False)
    expected = -daycount.candidate_refund(period, raise_by, daycount.DayCount.ACT_ACT)
    (change,) = support.transactions(demo_sut.client, sub["id"], "CHANGE")
    observed = support.money(change["net"])
    assert Decimal(0) < observed < raise_by
    assert observed == expected, f"pro-rata delta {observed}, oracle {expected}"
    assert _grid_amount(page, demo_sut.base_url, sub["id"], "CHANGE") == expected
    return f"delta {observed} for 16 of 31 days"


def _drive_payment_change(page, demo_sut, demo_control, demo_seed) -> str:
    sub = support.booked(demo_seed)
    new_account = make_iban(bban="UIPAY0000000061")
    demo_control.settings(account_check_mismatch_once=[new_account])
    rest = demo_seed.draft(sub["id"], "change")
    method = {"type": "DIRECT_DEBIT", "account": new_account}
    demo_sut.client.ok(
        "PATCH", f"/api/drafts/{rest['id']}", {"changes": {"payment_method": method}}
    )
    draft = DraftScreen(page, demo_sut.base_url).open(rest["id"])
    result = draft.confirm("confirm_change")
    assert result.outcome.status == 200, result.outcome.describe()

    after = support.latest(demo_sut.client, sub["id"])
    check = after["account_check"]
    assert after["payment_method"] == method
    assert check["result"] == "NO_MATCH" and check["name"] == after["holder"]["name"], check
    (change,) = support.transactions(demo_sut.client, sub["id"], "CHANGE")
    assert change["net"] == 0, "a payment-method change must not move money"
    return f"account {new_account} stored, name check {check['result']} for {check['name']}"


@pytest.mark.parametrize("driver", ["partial-credit", "price-delta", "payment-change"])
def test_money_path_drivers(demo_page, demo_sut, demo_control, demo_seed, driver, record_property):
    demo_control.freeze(support.NOW)
    if driver == "partial-credit":
        evidence = _drive_partial_credit(demo_page, demo_sut, demo_seed)
    elif driver == "price-delta":
        evidence = _drive_price_delta(demo_page, demo_sut, demo_seed)
    else:
        evidence = _drive_payment_change(demo_page, demo_sut, demo_control, demo_seed)
    record_property("money_path", evidence)
