"""Customer-side changes under an existing subscription, as one parametrised example.

The subscription shows its holder from the current customer record, so an edit to the
customer is visible on the subscription at once, without a change; moving the subscription
to another customer is a change and needs the new owner's acceptance. Each variant first
proves its reader can see the value it is about to watch move (the positive control).
"""

from __future__ import annotations

import pytest

from tests.demo_examples.change.oracles import is_blank
from tests.demo_examples.change.support import (
    active_subscription,
    current,
    finish_change,
    open_change,
)


def _company_subscription(seed, control, now: str) -> dict:
    control.freeze(f"{now}T09:00:00Z")
    company = seed.customer(type="COMPANY", display_name="Ledgerline Works")
    return active_subscription(seed, control, now=now, start="2026-06-01", customer=company)


@pytest.mark.parametrize("variant", ["rename", "email", "ownership_transfer", "reverify"])
def test_customer_change_variant(demo_seed, demo_control, variant):
    sub = _company_subscription(demo_seed, demo_control, now="2026-07-14")
    holder = sub["holder"]
    customer_url = f"/api/customers/{holder['id']}"

    if variant == "rename":
        assert holder["name"] == "Ledgerline Works", "control: the reader sees the name"
        demo_seed.client.ok("PATCH", customer_url, {"display_name": "Ledgerline Works Group"})
        assert current(demo_seed, sub["id"])["holder"]["name"] == "Ledgerline Works Group"
        assert current(demo_seed, sub["id"])["version"] == sub["version"], "no change needed"

    elif variant == "email":
        assert not is_blank(holder["email"]), "control: the e-mail is readable"
        demo_seed.client.ok("PATCH", customer_url, {"email": "accounts@ledgerline.example"})
        assert current(demo_seed, sub["id"])["holder"]["email"] == "accounts@ledgerline.example"

    elif variant == "ownership_transfer":
        other = demo_seed.customer(display_name="Ledgerline Successor")
        assert other["id"] != holder["id"] and not is_blank(holder["name"]), "control"
        draft = open_change(demo_seed, sub["id"], {"customer_id": other["id"]})
        assert draft["steps"][-1] == "ownership_acceptance"
        demo_seed.client.ok("POST", f"/api/drafts/{draft['id']}/actions/accept-ownership")
        view = finish_change(demo_seed, draft)
        assert (view["holder"]["id"], view["holder"]["name"]) == (
            other["id"],
            other["display_name"],
        )
        assert view["customer_id"] == other["id"]

    else:  # reverify
        before = demo_seed.client.ok("GET", customer_url)["verified_at"]
        assert before, "control: the verification time is readable"
        demo_control.shift(days=1)
        demo_seed.client.ok("POST", f"{customer_url}/actions/verify")
        after = demo_seed.client.ok("GET", customer_url)["verified_at"]
        assert after > before, f"verifying again must move the time: {before} -> {after}"

    assert current(demo_seed, sub["id"])["status"] == "active"
