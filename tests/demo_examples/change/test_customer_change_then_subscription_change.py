"""Edit the linked customer over REST (BillingClient), then move the subscription to it.

The customer record changes under an existing subscription; the subscription reads its
holder from the current record, and an ownership transfer still waits for acceptance.
"""

from __future__ import annotations

from api.clients.billing_client import BillingClient


def test_customer_edit_shows_at_once_and_a_transfer_waits_for_acceptance(
    demo_seed, demo_control, demo_http
):
    demo_control.freeze("2026-07-15T09:00:00Z")
    sub = demo_seed.subscription(start_date="2026-06-01")
    demo_seed.book_due(sub["id"])
    billing = BillingClient(demo_http)

    customer = billing.customer_of(sub["id"])
    assert customer["type"] == "INDIVIDUAL" and customer["id"] == sub["holder"]["id"]
    updated = billing.update_customer(customer["id"], type="COMPANY", display_name="Fernvale Co")
    assert updated["type"] == "COMPANY"
    holder = billing.subscription(sub["id"])["holder"]
    assert (holder["type"], holder["name"]) == ("COMPANY", "Fernvale Co")

    successor = demo_seed.customer(display_name="Fernvale Holdings")
    draft = billing.open_draft(sub["id"], "change", changes={"customer_id": successor["id"]})
    assert "ownership_acceptance" in draft["steps"]
    billing.accept_ownership(draft["id"])
    view = billing.confirm_draft(draft["id"])
    assert view["holder"]["id"] == successor["id"]
    assert [t["kind"] for t in billing.transactions(sub["id"])].count("CHANGE") == 1
