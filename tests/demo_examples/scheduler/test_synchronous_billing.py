"""Book a schedule synchronously instead of waiting for the background scheduler.

The demo registers one booking job per period, due on the period start. A test that needs
booked periods for a subscription starting in June would wait months for the scheduler;
``BillingRunner`` drives the demo's synchronous trigger (``POST /api/jobs/billing/run-next``)
instead and the transaction states flip in the same request.
"""

from __future__ import annotations

import pytest

from api.clients.billing_runner import BillingRunner

# The services this module exercises, for gate.service_selection (source "tag").
pytestmark = pytest.mark.service("scheduler", "billing")


def states(billing, sub_id):
    return [t["state"] for t in billing.transactions(sub_id)]


def test_bills_first_then_all_without_the_scheduler(demo_seed, demo_http, demo_sut, frozen):
    sub = demo_seed.subscription(start_date="2026-06-01")  # every period lies in the future
    billing = BillingRunner(demo_http)
    assert states(billing, sub["id"]) == ["SCHEDULED"] * 12
    assert billing.run_due(entity_id=sub["id"]) == []  # the scheduler books nothing yet

    first = billing.process_next_due(sub["id"])
    assert first["state"] == "BOOKED" and first["period_start"] == "2026-06-01"
    assert states(billing, sub["id"]) == ["BOOKED"] + ["SCHEDULED"] * 11

    rest = billing.process_all_due(sub["id"])
    assert len(rest) == 11 and all(t["state"] == "BOOKED" for t in rest)
    assert states(billing, sub["id"]) == ["BOOKED"] * 12
    assert billing.process_next_due(sub["id"]) is None  # nothing left
    invoices = demo_sut.client.ok("GET", f"/api/subscriptions/{sub['id']}/invoices")
    assert len(invoices) == 12
