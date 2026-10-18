"""ChargeLegs: fee-leg presence, 'charges no fee', and money hidden in the total."""

from decimal import Decimal as D

import pytest

from oracles.ledger_checks import ChargeLegs

HEALTHY_REFUND = {"net": -26.67, "tax": -5.33, "fee_net": 0.0, "fee_tax": 0.0, "total": -32.0}
FEE_CHARGED = {"net": -26.67, "tax": -5.33, "fee_net": 15.0, "fee_tax": 3.0, "total": -14.0}
# The fee legs vanished from the payload, but the money is still in the total.
FEE_VANISHED = {"net": -26.67, "tax": -5.33, "total": -14.0}
NO_FEE_LEGS = {"net": -26.67, "tax": -5.33, "total": -32.0}


def test_reads_every_leg_as_decimal():
    legs = ChargeLegs.of(FEE_CHARGED)
    assert legs == ChargeLegs(D("-26.67"), D("-5.33"), D("15.0"), D("3.0"), D("-14.0"))
    assert legs.price_and_total_read and legs.fee_leg_present


def test_healthy_refund_charges_no_fee():
    legs = ChargeLegs.of(HEALTHY_REFUND)
    assert legs.fee_leg_present
    assert legs.unexplained_total == D("0.00")
    assert legs.charges_no_fee() and legs.fee_legs_explain_total()


def test_fee_on_the_legs_is_charged():
    legs = ChargeLegs.of(FEE_CHARGED)
    assert legs.unexplained_total == D("18.00")
    assert legs.fee_legs_explain_total()
    assert not legs.charges_no_fee()


def test_absent_fee_leg_counts_as_zero_but_the_total_still_tells():
    legs = ChargeLegs.of(FEE_VANISHED)
    assert not legs.fee_leg_present
    assert legs.fee_net_or_zero == 0 and legs.fee_tax_or_zero == 0
    assert legs.unexplained_total == D("18.00")
    assert not legs.charges_no_fee(), "a vanished fee leg must not hide money in the total"
    assert not legs.fee_legs_explain_total()


def test_absent_fee_leg_with_a_clean_total_charges_no_fee():
    legs = ChargeLegs.of(NO_FEE_LEGS)
    assert not legs.fee_leg_present and legs.charges_no_fee()


@pytest.mark.parametrize("missing", ["net", "tax", "total"])
def test_unreadable_row_is_never_judged_fee_free(missing):
    row = {k: v for k, v in HEALTHY_REFUND.items() if k != missing}
    legs = ChargeLegs.of(row)
    assert not legs.price_and_total_read
    assert legs.unexplained_total is None
    assert not legs.charges_no_fee() and not legs.fee_legs_explain_total()


def test_tolerance_is_a_cent_by_default():
    row = dict(HEALTHY_REFUND, total=-31.99)
    assert ChargeLegs.of(row).charges_no_fee()
    assert not ChargeLegs.of(dict(HEALTHY_REFUND, total=-31.98)).charges_no_fee()


def test_custom_field_names():
    row = {"premium": "10.00", "premium_tax": "2.00", "cost": "1.50", "total_amount": "13.50"}
    legs = ChargeLegs.of(
        row, price_net="premium", price_tax="premium_tax", fee_net="cost", total="total_amount"
    )
    assert legs.fee_net == D("1.50") and legs.fee_tax is None
    assert legs.fee_legs_explain_total() and not legs.charges_no_fee()


def test_describe_names_presence_and_unexplained_money():
    text = ChargeLegs.of(FEE_VANISHED).describe()
    assert "absent (counted as zero)" in text and "18.00" in text
