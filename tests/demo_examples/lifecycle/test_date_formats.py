"""A start date on 29 February of a leap year is a date like any other (detector pair)."""

from __future__ import annotations

import pytest

from tests.demo_examples import detector_pair
from tests.demo_examples.lifecycle import support

TICKET = "DEMO-105"


@pytest.mark.parametrize("fault_on", [True, False], ids=["fault-on", "fault-off"])
def test_leap_day_start_detector(demo_http, demo_seed, demo_control, frozen, fault_on):
    demo_control.faults(*(["date_parser_rejects_leap_day"] if fault_on else []))
    customer = demo_seed.customer()
    body = {"customer_id": customer["id"], "partner_id": 1, "plan_id": demo_seed.plan_id()}
    leap = demo_http.post("subscriptions", json={**body, "start_date": "2028-02-29"})

    def exercised() -> None:
        # the day before is accepted with or without the fault: the create path works
        plain = support.ok(
            demo_http.post("subscriptions", json={**body, "start_date": "2028-02-28"})
        )
        assert plain["start_date"] == "2028-02-28"

    def oracle() -> None:
        assert leap.status == 201, (
            f"{TICKET}: a start on 2028-02-29 was refused ({leap.status}): {leap.text[:200]}"
        )
        created = leap.json()
        assert created["start_date"] == "2028-02-29"
        assert created["term_end"] == "2029-02-28", "the term end follows the month-end rule"

    detector_pair.judge(
        demo_control,
        ticket=TICKET,
        signature=rf"{TICKET}: a start on 2028-02-29 was refused",
        oracle=oracle,
        exercised=exercised,
        fault="date_parser_rejects_leap_day",
        fault_on=fault_on,
    )
