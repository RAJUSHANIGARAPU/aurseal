"""Renewal indexation: the renewal job's price, term and booked amounts against the oracle."""

from __future__ import annotations

from datetime import date
from decimal import Decimal as D

import pytest

from oracles.charges import READINGS, explain_indexed, indexed_amount, indexed_renewal
from tests.demo_examples import detector_pair
from tests.demo_examples.lifecycle import support
from verification._temporal import add_years_clamped

INDEX = D("0.03")  # an invented index factor for this example


def test_renewal_job_applies_the_index_factor(demo_http, frozen, seed_renewable_subscription):
    frozen.settings(index_percent=float(INDEX * 100))
    seeded = seed_renewable_subscription(frequency="YEARLY")
    sub_id = seeded["subscription_id"]
    before = support.subscription(demo_http, sub_id)
    base = D(str(before["price"]["monthly_net"]))

    result = support.ok(demo_http.post("jobs/renewals/run"))
    assert sub_id in result["renewed"]

    after = support.subscription(demo_http, sub_id)
    observed = D(str(after["price"]["monthly_net"]))
    assert explain_indexed(observed, base, INDEX) == "indexed", observed
    assert observed == indexed_amount(base, INDEX)
    old_end = date.fromisoformat(before["term_end"])
    assert after["term_end"] == add_years_clamped(old_end, 1).isoformat()

    (renew,) = [t for t in support.transactions(demo_http, sub_id) if t["kind"] == "RENEW"]
    expected = indexed_renewal(support.DEMO_CHARGES, base, INDEX, 12)
    assert (D(str(renew["net"])), D(str(renew["tax"]))) == (expected.net, expected.tax)


def test_a_leap_day_start_ends_its_terms_on_28_february(demo_http, demo_seed, demo_control):
    demo_control.freeze("2025-02-10T09:00:00Z")
    sub = demo_seed.subscription(start_date="2024-02-29", frequency="YEARLY")
    assert sub["term_end"] == add_years_clamped(date(2024, 2, 29), 1).isoformat() == "2025-02-28"
    support.book_due(demo_http, sub["id"])
    draft = support.open_renewal(demo_http, sub["id"])
    renewed = support.confirm_draft(demo_http, draft["id"])
    assert renewed["term_start"] == "2025-02-28" and renewed["term_end"] == "2026-02-28"


@pytest.mark.parametrize("fault_on", [True, False], ids=["fault-on", "fault-off"])
def test_misread_is_named_when_the_percent_is_truncated(
    demo_http, frozen, seed_renewable_subscription, fault_on
):
    seeded = seed_renewable_subscription()
    frozen.faults(*(["index_percent_truncated"] if fault_on else []))
    sub_id = seeded["subscription_id"]
    draft = support.open_renewal(demo_http, sub_id, index_percent=2.5)
    preview = draft["preview"]
    base, observed = D(str(preview["current_monthly_net"])), D(str(preview["new_monthly_net"]))
    reading = explain_indexed(observed, base, D("0.025"))

    def exercised() -> None:
        # a whole percentage cannot be truncated: indexed correctly with or without the fault
        support.ok(demo_http.post(f"drafts/{draft['id']}/actions/discard"))
        whole = support.open_renewal(demo_http, sub_id, index_percent=3)["preview"]
        assert explain_indexed(D(str(whole["new_monthly_net"])), base, D("0.03")) == "indexed"

    def oracle() -> None:
        assert reading == "indexed", (
            f"DEMO-106: the renewed price {observed} reads as {READINGS[reading]}"
        )

    detector_pair.judge(
        frozen,
        ticket="DEMO-106",
        signature=r"DEMO-106: the renewed price \S+ reads as index percent truncated",
        oracle=oracle,
        exercised=exercised,
        fault="index_percent_truncated",
        fault_on=fault_on,
    )
