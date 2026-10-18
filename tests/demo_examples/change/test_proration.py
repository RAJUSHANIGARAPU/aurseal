"""Mid-period price changes: one CHANGE transaction worth the pro-rata share of the delta."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

import pytest

from oracles.charges import ChargeSchedule, sign_matches
from oracles.period_window import Window, choose_mid_period_date
from tests.demo_examples import detector_pair
from tests.demo_examples.change.support import (
    active_subscription,
    change_transactions,
    finish_change,
    open_change,
)

#: The demo bills monthly here, with no frequency surcharge and 20 % tax.
MONTHLY = ChargeSchedule({1: Decimal(0)}, tax_rate=Decimal("0.20"), period_months=1)


def last_booked_window(seed, sub_id: int) -> Window:
    booked = [t for t in seed.transactions(sub_id) if t["state"] == "BOOKED" and t["kind"] == "NEW"]
    assert booked, "precondition: a booked period"
    last = booked[-1]
    end = date.fromisoformat(last["period_end"]) - timedelta(days=1)
    return Window(date.fromisoformat(last["period_start"]), end)


def raise_mid_period(seed, control, *, now: str, plan_kind: str, raise_by: Decimal, **fields):
    sub = active_subscription(
        seed, control, now=now, start="2026-06-01", plan_kind=plan_kind, **fields
    )
    window = last_booked_window(seed, sub["id"])
    chosen = choose_mid_period_date(window, days_into=9)
    assert chosen, "no date strictly inside the booked period"
    before = {t["id"]: t for t in seed.transactions(sub["id"])}
    old_net = Decimal(str(sub["price"]["monthly_net"]))
    draft = open_change(
        seed, sub["id"], {"monthly_net": float(old_net + raise_by)}, effective_date=str(chosen)
    )
    view = finish_change(seed, draft)
    return sub, view, chosen, before, old_net


@pytest.mark.parametrize("plan_kind", ["TEAM", "INDIVIDUAL"])
def test_a_mid_period_raise_books_one_change_under_the_ceiling(demo_seed, demo_control, plan_kind):
    raise_by = Decimal("4.20")
    sub, view, chosen, before, old_net = raise_mid_period(
        demo_seed,
        demo_control,
        now="2026-08-17",
        plan_kind=plan_kind,
        raise_by=raise_by,
        addons={"audit_trail": True},
    )
    assert sub["addons"]["audit_trail"] is True, "precondition: an add-on is on"
    rows = change_transactions(demo_seed, sub["id"])
    assert len(rows) == 1, f"exactly one CHANGE transaction, found {len(rows)}"
    booked = Decimal(str(rows[0]["net"]))
    ceiling = MONTHLY.period_delta_ceiling(old_net, old_net + raise_by, 1)
    assert sign_matches(raise_by, booked), f"a raise books a positive delta, got {booked}"
    assert booked < ceiling, f"mid-period delta {booked} must stay under the ceiling {ceiling}"
    assert rows[0]["period_start"] == str(chosen)
    assert Decimal(str(view["price"]["monthly_net"])) == old_net + raise_by
    after = {t["id"]: t for t in demo_seed.transactions(sub["id"])}
    assert [after[i]["kind"] for i in set(after) - set(before)] == ["CHANGE"]
    assert set(before) <= set(after), "no existing transaction disappeared"


# -- a mid-period change must book a pro-rata share, never nothing ---------------------------


def change_on(seed, control, effective_offset: int, raise_by: Decimal) -> tuple[dict, dict]:
    sub = active_subscription(seed, control, now="2026-08-17", start="2026-06-01")
    window = last_booked_window(seed, sub["id"])
    effective = window.start + timedelta(days=effective_offset)
    old = Decimal(str(sub["price"]["monthly_net"]))
    draft = open_change(
        seed, sub["id"], {"monthly_net": float(old + raise_by)}, effective_date=str(effective)
    )
    finish_change(seed, draft)
    (row,) = change_transactions(seed, sub["id"])
    return row, {"window": window, "effective": effective}


def test_period_start_change_books_full_delta(demo_seed, demo_control):
    """A change on a period start covers the whole period: the full delta, fault or not."""
    row, _ = change_on(demo_seed, demo_control, 0, Decimal("6.00"))
    assert Decimal(str(row["net"])) == Decimal("6.00")


@pytest.mark.parametrize("fault_on", [True, False], ids=["fault-on", "fault-off"])
def test_mid_period_change_is_prorated_detector(demo_seed, demo_control, fault_on):
    demo_control.faults(*(["proration_floors_to_zero"] if fault_on else []))
    raise_by = Decimal("6.00")
    row, where = change_on(demo_seed, demo_control, 10, raise_by)
    window, effective = where["window"], where["effective"]
    booked = Decimal(str(row["net"]))

    def exercised() -> None:
        full, _ = change_on(demo_seed, demo_control, 0, raise_by)
        assert Decimal(str(full["net"])) == raise_by, "control: a period-start change books it all"
        assert window.start < effective <= window.end

    def oracle() -> None:
        days = (window.end - window.start).days + 1
        remaining = (window.end - effective).days + 1
        expected = (raise_by * remaining / days).quantize(Decimal("0.01"), ROUND_HALF_UP)
        assert Decimal(0) < booked < raise_by, (
            f"DEMO-103: a mid-period change booked {booked}, outside (0, {raise_by})"
        )
        assert booked == expected, f"pro-rata share {expected}, booked {booked}"

    detector_pair.judge(
        demo_control,
        ticket="DEMO-103",
        signature=r"DEMO-103: a mid-period change booked 0(\.0+)?,",
        oracle=oracle,
        exercised=exercised,
        fault="proration_floors_to_zero",
        fault_on=fault_on,
    )
