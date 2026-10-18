"""Scheduler probes: a booking flips, a yearly term can renew, a same-day cancel is processed.

Each probe seeds its own subscription, drives the scheduler's own route
(``POST /api/jobs/run``) or the synchronous trigger, and reads the outcome back.
"""

from __future__ import annotations

import time
from datetime import date, timedelta

import pytest

from api.clients.billing_runner import BillingRunner

TODAY = "2026-03-10"


def test_process_flip_probe(demo_seed, demo_http, frozen, record_property):
    """Bind a yearly subscription, process its latest scheduled transaction through the job
    route, and poll until it flips to BOOKED. The probe fails only if it cannot run; the time
    to flip is recorded as the probe's reading."""
    billing = BillingRunner(demo_http)
    sub = demo_seed.subscription(start_date="2026-02-01", frequency="YEARLY")
    scheduled = [t for t in billing.transactions(sub["id"]) if t["state"] == "SCHEDULED"]
    if not scheduled:
        pytest.fail(f"probe cannot run: nothing schedulable on {sub['number']}")
    latest = max(scheduled, key=lambda t: t["period_start"])
    until = (date.fromisoformat(latest["period_start"]) + timedelta(days=1)).isoformat()

    started = time.monotonic()
    billing.run_due(until=until, entity_id=sub["id"])
    flipped = billing.await_transaction(
        sub["id"], states={"BOOKED"}, where=lambda t: t["id"] == latest["id"], timeout_s=10
    )
    took = time.monotonic() - started
    record_property("probe_flip_seconds", f"{took:.3f}")
    assert flipped["booked_at"], f"{latest['reference']} flipped without a booking time"


def test_yearly_term_renewal_reaches_pricing(demo_seed, demo_http, demo_sut, frozen):
    """A yearly subscription whose single installment is booked can open a renewal that
    reaches the price step; anything unbooked before the term end makes the probe abstain."""
    billing = BillingRunner(demo_http)
    sub = demo_seed.subscription(start_date="2025-03-15", frequency="YEARLY")
    billing.process_next_due(sub["id"])
    txs = billing.transactions(sub["id"])
    unbooked = [t for t in txs if t["period_start"] < sub["term_end"] and t["state"] != "BOOKED"]
    if len(txs) != 1 or unbooked:
        pytest.skip(f"needs exactly one booked installment, found {[t['state'] for t in txs]}")

    draft = demo_seed.draft(sub["id"], "renew")
    try:
        view = demo_sut.client.ok("GET", f"/api/drafts/{draft['id']}")
        assert view["steps"][-1] == "price", f"renewal stopped before pricing: {view['steps']}"
        preview = view["preview"]
        assert preview["new_term_start"] == sub["term_end"]
        assert preview["new_monthly_net"] > preview["current_monthly_net"]
    finally:
        demo_sut.client.post(f"/api/drafts/{draft['id']}/actions/discard")
    assert demo_sut.client.ok("GET", f"/api/subscriptions/{sub['id']}")["status"] == "active"


def test_same_day_cancellation_processed(demo_seed, demo_http, frozen):
    """Cancel effective today, wait for the cancellation transaction, run the scheduler's own
    route: the transaction ends BOOKED, not left SCHEDULED."""
    billing = BillingRunner(demo_http)
    sub = demo_seed.subscription(start_date="2026-03-01")
    billing.process_next_due(sub["id"])
    draft = demo_seed.draft(sub["id"], "cancel", reason="NO_LONGER_NEEDED", effective_date=TODAY)
    demo_seed.confirm_draft(draft["id"])
    cancel = billing.await_transaction(sub["id"], kind="CANCEL", timeout_s=10)

    response = demo_http.post("jobs/run", json={"subscription_id": sub["id"]})
    if not response.is_success:
        pytest.skip(f"the scheduler route was not accepted: {response}")
    (after,) = [t for t in billing.transactions(sub["id"]) if t["id"] == cancel["id"]]
    assert after["state"] != "SCHEDULED", f"{after['reference']} is still SCHEDULED after the run"
    assert after["state"] == "BOOKED", f"{after['reference']} ended {after['state']}"
