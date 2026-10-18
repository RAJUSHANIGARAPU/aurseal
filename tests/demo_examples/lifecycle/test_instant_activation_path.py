"""Both creation paths end active; the immediate one skips the order summary and payment."""

from __future__ import annotations

import pytest

from tests.demo_examples.lifecycle import support


@pytest.mark.parametrize("activate_immediately", [False, True])
def test_creation_paths_end_active(demo_http, demo_seed, frozen, activate_immediately):
    customer = demo_seed.customer()
    body = {
        "customer_id": customer["id"],
        "partner_id": 1,
        "plan_id": demo_seed.plan_id("TEAM"),
        "start_date": support.TODAY.isoformat(),
        "activate_immediately": activate_immediately,
    }
    sub = support.ok(demo_http.post("subscriptions", json=body))
    seen = [sub["status"]]
    if sub["status"] == "draft":
        sub = support.ok(demo_http.post(f"subscriptions/{sub['id']}/actions/submit", json={}))
        seen.append(sub["status"])
        sub = support.ok(demo_http.post(f"subscriptions/{sub['id']}/actions/confirm"))
        seen.append(sub["status"])

    assert seen == (["active"] if activate_immediately else ["draft", "pending_payment", "active"])
    assert support.transactions(demo_http, sub["id"]), "an active subscription has a schedule"
    templates = {
        d["template"] for d in support.ok(demo_http.get(f"subscriptions/{sub['id']}/documents"))
    }
    assert "account_opened" in templates
    assert ("order_summary" in templates) is not activate_immediately
