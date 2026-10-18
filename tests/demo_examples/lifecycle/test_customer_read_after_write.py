"""A customer read straight after an update returns what was written (detector pair)."""

from __future__ import annotations

import pytest

from tests.demo_examples import detector_pair
from tests.demo_examples.lifecycle import support

TICKET = "DEMO-111"


@pytest.mark.parametrize("fault_on", [True, False], ids=["fault-on", "fault-off"])
def test_customer_read_after_write_detector(demo_http, demo_seed, demo_control, frozen, fault_on):
    demo_control.faults(*(["customer_read_cached_after_update"] if fault_on else []))
    customer = demo_seed.customer(email="first.address@example.test")
    url = f"customers/{customer['id']}"
    support.ok(demo_http.get(url))  # an earlier read, as a screen would make
    support.ok(demo_http.patch(url, json={"email": "second.address@example.test"}))
    read_back = support.ok(demo_http.get(url))

    def exercised() -> None:
        # the uncached list shows the new value: the write happened, so a stale read is a read bug
        listed = [c for c in support.ok(demo_http.get("customers")) if c["id"] == customer["id"]]
        assert listed and listed[0]["email"] == "second.address@example.test", listed

    def oracle() -> None:
        assert read_back["email"] == "second.address@example.test", (
            f"{TICKET}: the customer read after an update returned {read_back['email']!r}"
        )

    detector_pair.judge(
        demo_control,
        ticket=TICKET,
        signature=rf"{TICKET}: the customer read after an update returned",
        oracle=oracle,
        exercised=exercised,
        fault="customer_read_cached_after_update",
        fault_on=fault_on,
    )
