"""New customers start unverified; the prerequisite's after-hook completes the gate."""

from __future__ import annotations

from test_data.fixture_replay import FixtureSeeder
from tests.demo_examples.lifecycle import support


def test_unverified_customer_is_rejected_and_verified_one_accepted(
    demo_http, replay: FixtureSeeder, verified_customer
):
    plain = replay.replay(
        support.load_fixture("customer-standard"),
        {"SEED_TAG": "unverified", "payout_account": support.new_account_number()},
    )
    assert plain["status"] == "pending_verification"
    body = {"partner_id": 1, "plan_id": 1}
    refused = demo_http.post("subscriptions", json={**body, "customer_id": plain["id"]})
    assert refused.status == 422
    assert refused.json()["message"] == "Customer is not verified."

    assert verified_customer["status"] == "verified"
    accepted = demo_http.post(
        "subscriptions", json={**body, "customer_id": verified_customer["id"]}
    )
    assert accepted.status == 201, accepted
    assert accepted.json()["holder"]["id"] == verified_customer["id"]
