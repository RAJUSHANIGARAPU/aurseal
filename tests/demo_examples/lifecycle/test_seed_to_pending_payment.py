"""A multi-step seed fixture replayed with live ids ends in pending_payment."""

from __future__ import annotations

from test_data.fixture_replay import unbound_placeholders
from tests.demo_examples.lifecycle import support

PARAMS = {"customer_id", "partner_id", "plan_id", "frequency", "debit_account"}


def test_fixture_binds_every_placeholder_offline():
    fixture = support.load_fixture("subscription-to-pending-payment")
    assert unbound_placeholders(fixture, PARAMS) == set()
    assert unbound_placeholders(fixture, PARAMS - {"plan_id"}) == {"plan_id"}


def test_seed_ends_pending_payment_with_every_step_bound(
    demo_http, seeded_pending_subscription, verified_customer
):
    seeded = seeded_pending_subscription
    assert seeded.skipped == []
    assert seeded["status"] == "pending_payment"
    sub = support.subscription(demo_http, seeded["subscription_id"])
    assert sub["number"] == seeded["subscription_number"]
    assert sub["holder"]["id"] == verified_customer["id"]
    assert sub["partner"]["name"] == "Seed Channel Partner"
    assert sub["frequency"] == "MONTHLY"
    assert sub["payment_method"]["type"] == "DIRECT_DEBIT" and sub["payment_method"]["account"]
    assert sub["start_date"] == support.TODAY.isoformat()
    assert sub["price"]["monthly_net"] == seeded["quoted_net"]
