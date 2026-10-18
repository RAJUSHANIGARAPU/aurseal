"""Read a subscription's fee block without ever turning "absent" into zero."""

from __future__ import annotations

import pytest

from test_data.shape import ShapeNotObserved
from tests.demo_examples.finance._fees import FeeShape
from tests.demo_examples.finance._support import BOOK_UNTIL, FROZEN_NOW, START_DATE


def test_subscription_fees_read(demo_control, demo_seed, demo_sut):
    client = demo_sut.client
    demo_control.freeze(FROZEN_NOW)
    sub = demo_seed.subscription(start_date=START_DATE)
    fees = FeeShape.of(client.ok("GET", f"/api/subscriptions/{sub['id']}"))
    assert fees.all_present(), fees.describe()
    assert fees.charges_fee() and not fees.all_zero()
    assert fees.shape.get("invoiced") is False, "nothing is booked yet"

    demo_seed.book_due(sub["id"], until=BOOK_UNTIL)
    booked = FeeShape.of(client.ok("GET", f"/api/subscriptions/{sub['id']}"))
    assert booked.shape.get("invoiced") is True, booked.describe()

    plan = client.ok(
        "POST",
        "/api/plans",
        {"code": "NO-SETUP", "kind": "TEAM", "base_monthly_net": 21.5, "setup_fee": 0},
    )
    free = demo_seed.subscription(start_date=START_DATE, state="draft", plan_id=plan["id"])
    no_fee = FeeShape.of(client.ok("GET", f"/api/subscriptions/{free['id']}"))
    assert no_fee.all_present() and no_fee.all_zero() and not no_fee.charges_fee()


@pytest.mark.parametrize(
    "body",
    [{"number": "SUB-X"}, {"number": "SUB-X", "fees": {}}, {"number": "SUB-X", "fees": {"tax": 0}}],
    ids=["no-fee-block", "empty-fee-block", "net-dropped"],
)
def test_absent_fee_is_never_read_as_zero(body):
    fees = FeeShape.of(body)
    assert not fees.all_present()
    with pytest.raises(ShapeNotObserved, match="not observed"):
        fees.charges_fee()
    with pytest.raises(ShapeNotObserved):
        fees.all_zero()
