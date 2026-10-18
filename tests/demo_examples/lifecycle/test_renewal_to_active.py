"""Renewal end to end for a fresh and a reused subscription."""

from __future__ import annotations

from datetime import timedelta

import pytest

from test_data.pool import select_targets
from test_data.seed_mode import resolve
from tests.demo_examples.lifecycle import support


@pytest.mark.parametrize("mode", ["fresh", "reuse"])
def test_renewal_runs_visible_steps_and_bills_the_renewal_first(
    demo_http, frozen, seed_renewable_subscription, mode
):
    seed = lambda: support.subscription(  # noqa: E731
        demo_http, seed_renewable_subscription()["subscription_id"]
    )
    if mode == "reuse":
        seed()  # an earlier run left a renewable subscription behind
    (target,) = select_targets(
        resolve({"SEED_MODE": mode}), support.renewable_pool(demo_http), seed
    )
    assert target.was_seeded is (mode == "fresh")
    sub = target.entity
    old_end = sub["term_end"]

    draft = support.open_renewal(demo_http, sub["id"])
    seen = {}
    ran = support.run_visible_steps(
        draft, {"indexation": lambda: seen.setdefault("index", draft["preview"]["index_percent"])}
    )
    assert ran == ["indexation"]  # the price step is prefilled and submitted as is
    assert "effective_date" not in draft["changes"]  # the start date is never retyped
    renewed = support.confirm_draft(demo_http, draft["id"])
    assert renewed["status"] == "renewed" and renewed["term_start"] == old_end

    support.book_due(demo_http, sub["id"], until=old_end)
    invoices = support.ok(demo_http.get(f"subscriptions/{sub['id']}/invoices"))
    first_new = min(
        (i for i in invoices if i["period_start"] >= old_end), key=lambda i: i["period_start"]
    )
    assert first_new["kind"] == "RENEW"
    assert first_new["document_date"] == support.TODAY.isoformat()
    assert first_new["due_date"] == (support.TODAY + timedelta(days=30)).isoformat()
    notices = [
        d
        for d in support.ok(demo_http.get(f"subscriptions/{sub['id']}/documents"))
        if d["template"] == "term_extension_notice"
    ]
    assert [d["version"] for d in notices] == [renewed["version"]]
    assert notices[0]["created_at"].startswith(support.TODAY.isoformat())
