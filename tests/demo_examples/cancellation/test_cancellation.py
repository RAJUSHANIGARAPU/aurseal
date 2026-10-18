"""Closure examples against demo_sut: the reason, the credit oracles and the closure notice.

Ending a subscription credits the unearned part of the periods already paid for. Where the
day-count convention is not what an example is about, it asserts only the bounds that hold
under every convention (:func:`oracles.daycount.assert_pro_rata_refund`). The exact examples
read the demo's ``day_count`` setting and predict the credit under that convention.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

import pytest

from oracles import pdf_text
from oracles.daycount import DayCount, candidate_refund, matched_conventions
from oracles.period_window import choose_mid_period_date
from oracles.reconciliation import reconcile
from test_data.pool import NoTarget
from tests.demo_examples import detector_pair
from tests.demo_examples.cancellation.support import (
    amount,
    assert_refund_within_bounds,
    cancel,
    last_paid,
    oracle_period,
    processed_subscription,
    reuse_targets,
)

#: The demo's ``day_count`` values in the oracle's vocabulary. The demo counts the unused
#: slice from the closure date to the next period start.
DEMO_CONVENTIONS = {"actual/period": DayCount.ACT_ACT, "30/360": DayCount.THIRTY_E_360}


def test_the_closure_reason_is_kept(demo_seed, demo_control):
    reason = "CONSOLIDATING"
    sub = processed_subscription(demo_seed, demo_control, now="2026-08-12", start="2026-06-01")
    assert sub["closure"] is None, "precondition: nothing has been closed yet"

    draft = demo_seed.draft(sub["id"], "cancel", effective_date="2026-08-24")
    assert draft["reason"] is None and draft["steps"] == ["details", "credit"]
    refused = demo_seed.client.post(f"/api/drafts/{draft['id']}/actions/confirm")
    assert refused.status == 422 and "reason" in refused.json()["fields"]

    saved = demo_seed.client.ok("PATCH", f"/api/drafts/{draft['id']}", {"reason": reason})
    assert saved["reason"] == reason, "an autosave must read the chosen reason back"
    view = demo_seed.confirm_draft(draft["id"])
    assert view["status"] == "cancelled" and view["closure"]["reason"] == reason
    stored = demo_seed.client.ok("GET", f"/api/subscriptions/{sub['id']}?version={view['version']}")
    assert stored["closure"]["reason"] == reason
    versions = demo_seed.client.ok("GET", f"/api/subscriptions/{sub['id']}/versions")
    assert versions[-1]["cause"] == "cancel"


def _close_mid_period(seed, control, *, now: str, start: str, days: int, plan_kind: str):
    sub = processed_subscription(seed, control, now=now, start=start, plan_kind=plan_kind)
    paid, window = last_paid(seed, sub["id"])  # read what was paid before acting
    target = date.fromisoformat(now) + timedelta(days=days)
    chosen = choose_mid_period_date(window, days_into=(target - window.start).days)
    assert chosen, "no date strictly inside the paid period"
    view, cancel_tx = cancel(seed, sub["id"], chosen)
    return sub, paid, chosen, view, cancel_tx


@pytest.mark.parametrize("plan_kind", ["TEAM", "INDIVIDUAL"])
def test_a_mid_period_credit_stays_within_the_bounds(demo_seed, demo_control, plan_kind):
    sub, paid, chosen, view, cancel_tx = _close_mid_period(
        demo_seed, demo_control, now="2026-09-03", start="2026-07-01", days=11, plan_kind=plan_kind
    )
    assert view["status"] == "cancelled" and sub["plan_kind"] == plan_kind
    assert_refund_within_bounds(f"{sub['number']} closed {chosen}", paid, chosen, view)
    assert amount(cancel_tx["net"]) == amount(view["closure"]["credit_net"])
    later = [
        t
        for t in demo_seed.transactions(sub["id"])
        if t["kind"] == "NEW" and t["period_start"] > chosen.isoformat()
    ]
    assert later and all(t["state"] == "VOID" for t in later), "unpaid later periods are voided"


@pytest.mark.parametrize("setting", sorted(DEMO_CONVENTIONS))
@pytest.mark.parametrize(
    ("start", "now", "effective"),
    [
        ("2026-05-10", "2026-07-12", "2026-07-21"),
        ("2026-05-01", "2026-07-12", "2026-07-30"),
        ("2026-05-20", "2026-07-25", "2026-08-02"),
    ],
)
def test_the_exact_credit_under_the_configured_convention(
    demo_seed, demo_control, setting, start, now, effective
):
    demo_control.settings(day_count=setting)
    configured = demo_seed.client.ok("GET", "/api/_admin/settings")["day_count"]
    convention = DEMO_CONVENTIONS[configured]  # read back, never assumed
    sub = processed_subscription(demo_seed, demo_control, now=now, start=start)
    paid, window = last_paid(demo_seed, sub["id"])
    closing = date.fromisoformat(effective)
    assert window.start < closing <= window.end, "precondition: the date falls inside the period"

    view, _ = cancel(demo_seed, sub["id"], closing)
    closure = view["closure"]
    assert closure["payee_account"], "a non-zero credit needs somewhere to go"
    period = oracle_period(paid, closing)
    assert amount(closure["credit_net"]) == candidate_refund(
        period, amount(paid["net"]), convention
    ), f"credit net under {convention.value}"
    assert amount(closure["credit_tax"]) == candidate_refund(
        period, amount(paid["tax"]), convention
    ), f"credit tax under {convention.value}"


def test_leap_february_closure_pins_the_convention(demo_seed, demo_control):
    """February 2028 has 29 days. Closing on the 15th leaves 15 of 29 actual days unused,
    but 16 of 30 days on a 30-day-month count, so the two settings give different credits
    and each must match its own prediction."""
    credits = {}
    for setting, convention in DEMO_CONVENTIONS.items():
        demo_control.settings(day_count=setting)
        sub = processed_subscription(demo_seed, demo_control, now="2028-02-05", start="2028-02-01")
        paid, _ = last_paid(demo_seed, sub["id"])
        assert (paid["period_start"], paid["period_end"]) == ("2028-02-01", "2028-03-01")
        closing = date(2028, 2, 15)
        view, _ = cancel(demo_seed, sub["id"], closing)
        credit = amount(view["closure"]["credit_net"])
        period = oracle_period(paid, closing)
        assert convention in matched_conventions(period, amount(paid["net"]), credit), (
            f"{setting}: credit {credit} is not the {convention.value} share"
        )
        credits[setting] = credit
    assert credits["actual/period"] != credits["30/360"], "the dates must tell the two apart"


# -- reuse + document reconciliation -------------------------------------------------------

NOTICE_LABELS = {"Credit:": "total", "Credit net:": "net", "Credit tax:": "tax"}


def _notice_text(client, sub_id: int) -> str:
    docs = client.ok("GET", f"/api/subscriptions/{sub_id}/documents")
    notices = [d for d in docs if d["template"] == "closure_notice"]
    assert len(notices) == 1, f"expected one closure notice, found {len(notices)}"
    response = client.get(f"/api/documents/{notices[0]['id']}.pdf")
    assert response.status == 200
    return pdf_text.extract_text(response.body)


def test_a_reused_subscription_closes_and_its_notice_reconciles(demo_seed, demo_control):
    if not pdf_text.available():
        pytest.skip("reading the document needs the optional 'pypdf' package")
    chosen = date(2026, 10, 19)
    targets = reuse_targets(demo_seed, demo_control, now="2026-10-08", chosen=chosen, limit=3)
    reconciled = []
    for target in targets:
        sub = target.entity
        view, cancel_tx = cancel(demo_seed, sub["id"], chosen)
        assert view["status"] == "cancelled", target.label
        text = _notice_text(demo_seed.client, sub["id"])
        legs = {leg: amount(cancel_tx[leg]) for leg in ("total", "net", "tax")}
        result = reconcile(text, legs, NOTICE_LABELS, locale="en_GB", expected_sign=-1)
        assert result.ok, f"{target.label}: {result.describe()}"
        assert f"Ends on: {chosen.isoformat()}" in text
        reconciled.append(target.label)
    assert reconciled, "nothing was closed"


def test_reused_subscriptions_close_within_the_bounds(demo_seed, demo_control):
    chosen = date(2026, 11, 17)
    try:
        targets = reuse_targets(demo_seed, demo_control, now="2026-11-06", chosen=chosen, limit=15)
    except NoTarget as why:
        pytest.skip(f"neither borrowing nor building a subscription worked: {why}")
    closed = []
    for target in targets:
        sub = target.entity
        paid, _ = last_paid(demo_seed, sub["id"])
        view, cancel_tx = cancel(demo_seed, sub["id"], chosen, reason="BUDGET")
        assert view["status"] == "cancelled", target.label
        assert cancel_tx["period_start"] == chosen.isoformat()
        assert_refund_within_bounds(f"{target.label} closed {chosen}", paid, chosen, view)
        closed.append(target.label)
    assert closed


# -- the credit against what was paid -------------------------------------------------------

CENT = Decimal("0.01")


def money_of(value) -> Decimal:
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def paid_for(tx: dict) -> Decimal:
    return amount(tx["net"]) + amount(tx["tax"])


def test_credit_equals_unearned_share(demo_seed, demo_control):
    """Mid-period, the credit is the unearned share of what was paid for that period."""
    sub = processed_subscription(demo_seed, demo_control, now="2026-09-03", start="2026-08-01")
    paid, window = last_paid(demo_seed, sub["id"])
    closing = window.start + timedelta(days=12)
    view, cancel_tx = cancel(demo_seed, sub["id"], closing)
    period = oracle_period(paid, closing)
    expected = candidate_refund(period, amount(paid["net"]), DayCount.ACT_ACT) + candidate_refund(
        period, amount(paid["tax"]), DayCount.ACT_ACT
    )
    assert amount(view["closure"]["credit_total"]) == expected
    assert abs(amount(cancel_tx["total"])) <= paid_for(paid)


@pytest.mark.parametrize("fault_on", [True, False], ids=["fault-on", "fault-off"])
def test_credit_never_exceeds_paid_detector(demo_seed, demo_control, fault_on):
    demo_control.faults(*(["refund_tax_applied_twice"] if fault_on else []))
    sub = processed_subscription(demo_seed, demo_control, now="2026-09-03", start="2026-08-01")
    paid, window = last_paid(demo_seed, sub["id"])
    view, _ = cancel(demo_seed, sub["id"], window.start)  # the whole period comes back
    credit = abs(amount(view["closure"]["credit_total"]))

    def exercised() -> None:
        # a closure on the term end has nothing unearned: zero, with or without the fault
        other = processed_subscription(
            demo_seed, demo_control, now="2026-09-03", start="2026-08-01"
        )
        on_term_end, _ = cancel(demo_seed, other["id"], other["term_end"])
        assert amount(on_term_end["closure"]["credit_total"]) == 0
        assert credit > 0, "the closure credited nothing, so the cap was never tested"

    def oracle() -> None:
        assert credit <= paid_for(paid), (
            f"DEMO-101: a closure credit of {credit} exceeds the {paid_for(paid)} paid for "
            f"{paid['period_start']}..{paid['period_end']}"
        )

    detector_pair.judge(
        demo_control,
        ticket="DEMO-101",
        signature=r"DEMO-101: a closure credit of \S+ exceeds the",
        oracle=oracle,
        exercised=exercised,
        fault="refund_tax_applied_twice",
        fault_on=fault_on,
    )


def splitting_day(paid: dict, window) -> date | None:
    """A closure day on which rounding the legs separately and rounding their sum differ."""
    net, tax = amount(paid["net"]), amount(paid["tax"])
    whole = (window.end - window.start).days + 1
    for offset in range(1, whole):
        left = Decimal(whole - offset) / whole
        if money_of(net * left) + money_of(tax * left) != money_of(net * left + tax * left):
            return window.start + timedelta(days=offset)
    return None


@pytest.mark.parametrize("fault_on", [True, False], ids=["fault-on", "fault-off"])
def test_credit_legs_sum_to_total(demo_seed, demo_control, fault_on):
    demo_control.faults(*(["refund_legs_rounded_independently"] if fault_on else []))
    sub = processed_subscription(demo_seed, demo_control, now="2026-08-20", start="2026-08-01")
    paid, window = last_paid(demo_seed, sub["id"])
    closing = splitting_day(paid, window)
    view, cancel_tx = (None, None) if closing is None else cancel(demo_seed, sub["id"], closing)

    def exercised() -> None:
        assert closing is not None, "no closure date splits the rounding: nothing to judge"
        assert view["status"] == "cancelled"

    def oracle() -> None:
        legs = amount(cancel_tx["net"]) + amount(cancel_tx["tax"])
        total = amount(cancel_tx["total"])
        assert legs == total, f"DEMO-102: the credit legs add up to {legs}, the total is {total}"

    detector_pair.judge(
        demo_control,
        ticket="DEMO-102",
        signature=r"DEMO-102: the credit legs add up to",
        oracle=oracle,
        exercised=exercised,
        fault="refund_legs_rounded_independently",
        fault_on=fault_on,
    )
