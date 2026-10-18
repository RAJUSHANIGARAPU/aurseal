"""A backdated seed makes a renewal due today; a stale draft is discarded and retried."""

from __future__ import annotations

from datetime import timedelta

import pytest

from test_data.drafts import open_draft
from tests.demo_examples.lifecycle import support


@pytest.mark.parametrize("frequency", ["YEARLY", "MONTHLY"])
def test_backdated_seed_is_billed_up_to_the_renewal_date(
    demo_http, seed_renewable_subscription, frequency
):
    seeded = seed_renewable_subscription(frequency=frequency)
    sub = support.subscription(demo_http, seeded["subscription_id"])
    assert sub["start_date"] == (support.TODAY - timedelta(days=365)).isoformat()
    assert sub["term_end"] == seeded["term_end"] == support.TODAY.isoformat()
    before_renewal = [
        t for t in support.transactions(demo_http, sub["id"]) if t["period_start"] < sub["term_end"]
    ]
    assert before_renewal and {t["state"] for t in before_renewal} == {"BOOKED"}
    assert len(before_renewal) == (1 if frequency == "YEARLY" else 12)
    draft = support.open_renewal(demo_http, sub["id"])
    assert draft["preview"]["new_term_start"] == sub["term_end"]


def test_renewal_draft_opens_after_discarding_a_stale_one(
    demo_http, seed_renewable_subscription, cleanup
):
    sub_id = seed_renewable_subscription()["subscription_id"]
    stale = support.ok(
        demo_http.post(f"subscriptions/{sub_id}/drafts", json={"kind": "change", "changes": {}})
    )  # abandoned by an earlier run

    def open_call():
        return demo_http.post(f"subscriptions/{sub_id}/drafts", json={"kind": "renew"})

    def discard(draft_id):
        return demo_http.post(f"drafts/{draft_id}/actions/discard")

    refused = open_call()
    assert refused.status == 409 and refused.json()["draft_id"] == stale["id"]
    draft_id = open_draft(
        open_call,
        draft_id=lambda r: str(r.json()["id"]),
        discard=discard,
        discard_stale=lambda: discard(refused.json()["draft_id"]),
        ledger=cleanup,
    )
    assert support.ok(demo_http.get(f"drafts/{stale['id']}"))["status"] == "DISCARDED"
    renewal = support.ok(demo_http.get(f"drafts/{draft_id}"))
    assert renewal["kind"] == "renew" and renewal["status"] == "OPEN"
    assert [(e.kind, e.ident) for e in cleanup.entries] == [("draft", draft_id)]
