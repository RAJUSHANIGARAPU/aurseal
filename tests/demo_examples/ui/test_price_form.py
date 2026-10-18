"""The price form's edges, in one browser session on a deep-linked price step."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

import pytest

from demo_sut.billing import PRICE_MESSAGES
from tests.demo_examples.ui import support
from ui.pages.demo.price_step import PriceStep
from verification.defect_detector import FIXED_TAG, REGRESSED_MARKER, still_reproduces
from verification.verdicts import RedVerdict

pytestmark = pytest.mark.demo_ui


def test_price_form_edge_cases(demo_page, demo_sut, demo_control, demo_seed):
    demo_control.freeze(support.NOW)
    sub = support.segment_subscription(demo_seed, "company", state="draft")
    price = PriceStep(demo_page, demo_sut.base_url).open(sub["id"])

    def calc(**values):
        for name, value in values.items():
            price.enter(name, value)
        return price.calculate()

    # zero tax: tax 0, gross = net
    assert calc(net="40.00", tax_rate="0").status == 200
    assert price.amount("tax") == Decimal("0.00") and price.amount("gross") == Decimal("40.00")

    # rounding half-up on the tax: 33.33 x 20 % = 6.666 -> 6.67
    assert calc(net="33.33", tax_rate="20").status == 200
    assert price.amount("tax") == Decimal("6.67") and price.amount("gross") == Decimal("40.00")

    # bounds: both limits accepted, one cent outside refused with the named message
    for inside, outside, message in (
        ("1.00", "0.99", PRICE_MESSAGES["minimum"]),
        ("10000.00", "10000.01", PRICE_MESSAGES["maximum"]),
    ):
        assert calc(net=inside).status == 200, inside
        refused = calc(net=outside)
        assert refused.status == 422 and price.field_error("net") == message, outside
        price.enter("net", "40.00")

    # commission: the cap is accepted, above it refused
    cap = demo_sut.client.ok("GET", "/api/_admin/settings")["commission_cap_percent"]
    assert calc(commission=str(cap)).status == 200
    assert price.amount("commission") == Decimal("40.00") * Decimal(str(cap)) / 100
    over = calc(commission=str(cap + 0.5))
    assert over.status == 422
    assert price.field_error("commission") == PRICE_MESSAGES["commission_cap"].format(
        cap=Decimal(str(cap))
    )


# -- detectors -----------------------------------------------------------------------------

NEGATIVE_DEFECT = "BUG-201"
NEGATIVE_SIGNATURE = r"a typed negative net amount was ACCEPTED by the price form"
ROUNDING_DEFECT = "BUG-202"
ROUNDING_SIGNATURE = r"tax \S+ is not the half-up rounding \S+ of net x rate"


def _price_step(demo_page, demo_sut, demo_seed) -> PriceStep:
    sub = support.segment_subscription(demo_seed, "company", state="draft")
    return PriceStep(demo_page, demo_sut.base_url).open(sub["id"])


def _control(price: PriceStep):
    """The form takes and computes an in-range amount: the mechanism is exercised."""

    def check():
        assert price.enter("net", "25.00") == "25.00"
        outcome = price.calculate()
        assert outcome.status == 200 and price.amount("net") == Decimal("25.00"), outcome.describe()

    return check


@pytest.mark.parametrize("fault_on", [False, True], ids=["healthy", "negative-price-accepted"])
def test_negative_price_detector_closed(demo_page, demo_sut, demo_control, demo_seed, fault_on):
    """Closed expectation: the refusal must hold; with the fault on it is a regression."""
    demo_control.freeze(support.NOW)
    if fault_on:
        demo_control.fault("negative_price_accepted")
    price = _price_step(demo_page, demo_sut, demo_seed)
    assert price.active_faults() == (["negative_price_accepted"] if fault_on else [])

    def oracle():
        price.enter_literal("net", "-5.00")
        outcome = price.calculate()
        assert outcome.rejected and price.field_error("net") == PRICE_MESSAGES["negative"], (
            f"{NEGATIVE_DEFECT}: a typed negative net amount was ACCEPTED by the price form "
            f"(status {outcome.status}, faults {demo_control.evidence()})"
        )

    if fault_on:
        with pytest.raises(AssertionError, match=REGRESSED_MARKER):
            still_reproduces(
                NEGATIVE_DEFECT, NEGATIVE_SIGNATURE, oracle, exercised=_control(price), closed=True
            )
    else:
        verdict = still_reproduces(
            NEGATIVE_DEFECT, NEGATIVE_SIGNATURE, oracle, exercised=_control(price), closed=True
        )
        assert verdict is RedVerdict.CLOSED_PASS
    assert demo_control.evidence() == ("negative_price_accepted" if fault_on else "none")


@pytest.mark.parametrize("fault_on", [True, False], ids=["rounding-drift", "healthy"])
def test_rounding_drift_detector(demo_page, demo_sut, demo_control, demo_seed, fault_on):
    """Open defect: reproduces while the fault is on; cleared (a loud red) when it is off."""
    demo_control.freeze(support.NOW)
    if fault_on:
        demo_control.fault("rounding_drift")
    price = _price_step(demo_page, demo_sut, demo_seed)

    def oracle():
        price.enter("net", "33.33")
        price.enter("tax_rate", "20")
        assert price.calculate().status == 200
        shown = price.amount("tax")
        expected = (Decimal("33.33") * 20 / 100).quantize(Decimal("0.01"), ROUND_HALF_UP)
        assert shown == expected, (
            f"{ROUNDING_DEFECT}: tax {shown} is not the half-up rounding {expected} of net x rate "
            f"(faults {demo_control.evidence()})"
        )

    if fault_on:
        verdict = still_reproduces(
            ROUNDING_DEFECT, ROUNDING_SIGNATURE, oracle, exercised=_control(price), closed=False
        )
        assert verdict is RedVerdict.REPRODUCES
    else:
        with pytest.raises(AssertionError, match=FIXED_TAG):
            still_reproduces(
                ROUNDING_DEFECT, ROUNDING_SIGNATURE, oracle, exercised=_control(price), closed=False
            )
    assert demo_control.evidence() == ("rounding_drift" if fault_on else "none")
