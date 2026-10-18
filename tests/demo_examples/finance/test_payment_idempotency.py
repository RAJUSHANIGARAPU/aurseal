"""A payment sent twice with one idempotency key is recorded once."""

from __future__ import annotations

from decimal import Decimal

import pytest

from tests.demo_examples import detector_pair
from tests.demo_examples.finance._support import FROZEN_NOW, START_DATE

TICKET = "DEMO-107"


@pytest.mark.parametrize("fault_on", [True, False], ids=["fault-on", "fault-off"])
def test_replayed_payment_counted_once(demo_control, demo_seed, demo_sut, fault_on):
    demo_control.faults(*(["payment_replay_double_counted"] if fault_on else []))
    demo_control.freeze(FROZEN_NOW)
    sub = demo_seed.subscription(start_date=START_DATE)
    demo_seed.book_due(sub["id"])
    (invoice,) = demo_sut.client.ok("GET", f"/api/subscriptions/{sub['id']}/invoices")
    url = f"/api/invoices/{invoice['id']}/pay"
    part = {"amount": 12.0}

    first = demo_sut.client.post(url, part, headers={"Idempotency-Key": f"once-{sub['id']}"})
    replay = demo_sut.client.post(url, part, headers={"Idempotency-Key": f"once-{sub['id']}"})
    after_replay = demo_sut.client.ok("GET", f"/api/invoices/{invoice['id']}")

    def exercised() -> None:
        # two different keys are two payments, with or without the fault
        a = demo_sut.client.post(url, {"amount": 1.0}, headers={"Idempotency-Key": "k-a"}).json()
        b = demo_sut.client.post(url, {"amount": 1.0}, headers={"Idempotency-Key": "k-b"}).json()
        assert a["payment"]["id"] != b["payment"]["id"]
        assert first.status == 200 and first.json()["payment"]["amount"] == 12.0

    def oracle() -> None:
        expected = Decimal(str(invoice["total"])) - Decimal("12.00")
        assert replay.status == 200
        assert replay.json()["payment"]["id"] == first.json()["payment"]["id"], (
            f"{TICKET}: the replayed payment was recorded again as payment "
            f"{replay.json()['payment']['id']}"
        )
        assert Decimal(str(after_replay["remaining"])) == expected

    detector_pair.judge(
        demo_control,
        ticket=TICKET,
        signature=rf"{TICKET}: the replayed payment was recorded again",
        oracle=oracle,
        exercised=exercised,
        fault="payment_replay_double_counted",
        fault_on=fault_on,
    )
