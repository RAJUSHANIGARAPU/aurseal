"""The money legs of a real closure transaction, read with :class:`ChargeLegs`."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from oracles.ledger_checks import ChargeLegs
from tests.demo_examples.cancellation.support import cancel, last_paid, processed_subscription
from tests.demo_examples.cancellation.test_cancellation import splitting_day


def test_credit_legs_are_explained(demo_seed, demo_control):
    sub = processed_subscription(demo_seed, demo_control, now="2026-09-09", start="2026-08-01")
    _, cancel_tx = cancel(demo_seed, sub["id"], date(2026, 9, 23))
    legs = ChargeLegs.of(cancel_tx)
    assert legs.price_and_total_read and legs.fee_leg_present, legs.describe()
    assert legs.price_net is not None and legs.price_net < 0, "a credit is booked negative"
    assert legs.fee_legs_explain_total(), legs.describe()
    assert legs.charges_no_fee(), legs.describe()


@pytest.mark.parametrize("fault_on", [True, False], ids=["fault-on", "fault-off"])
def test_legs_explain_the_total_to_the_cent(demo_seed, demo_control, fault_on):
    """With no tolerance at all, the legs either explain the total or the gap is one cent."""
    demo_control.faults(*(["refund_legs_rounded_independently"] if fault_on else []))
    sub = processed_subscription(demo_seed, demo_control, now="2026-08-20", start="2026-08-01")
    paid, window = last_paid(demo_seed, sub["id"])
    closing = splitting_day(paid, window)
    assert closing, "precondition: a date that splits the rounding"
    _, cancel_tx = cancel(demo_seed, sub["id"], closing)
    legs = ChargeLegs.of(cancel_tx)
    assert legs.fee_legs_explain_total(tolerance=Decimal(0)) is not fault_on, legs.describe()
    if fault_on:
        assert abs(legs.unexplained_total) == Decimal("0.01"), legs.describe()
