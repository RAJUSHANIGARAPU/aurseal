"""A future-dated change: scheduled now, in effect only from its effective date."""

from __future__ import annotations

from decimal import Decimal

from tests.demo_examples.change.support import (
    active_subscription,
    change_transactions,
    current,
    finish_change,
    open_change,
)


def test_future_change_schedules_from_effective_date(demo_seed, demo_control):
    sub = active_subscription(demo_seed, demo_control, now="2026-04-24", start="2026-03-01")
    old_version, old_net = sub["version"], sub["price"]["monthly_net"]
    effective = "2026-06-15"  # in the future, mid-period

    draft = open_change(
        demo_seed, sub["id"], {"monthly_net": old_net + 10}, effective_date=effective
    )
    assert draft["id"], "the change returns an id"
    new = finish_change(demo_seed, draft)
    assert new["version"] == old_version + 1 and new["version_status"] == "scheduled"

    rows = change_transactions(demo_seed, sub["id"])
    assert len(rows) == 1
    assert rows[0]["period_start"] == effective
    assert Decimal(str(rows[0]["net"])) != 0, "a price move schedules a non-zero transaction"
    assert rows[0]["state"] == "SCHEDULED"

    today = current(demo_seed, sub["id"])
    assert today["version"] == old_version, "the current version stays until the effective date"
    assert today["price"]["monthly_net"] == old_net and today["latest_version"] == new["version"]

    demo_control.freeze(f"{effective}T09:00:00Z")
    then = current(demo_seed, sub["id"])
    assert then["version"] == new["version"] and then["price"]["monthly_net"] == old_net + 10
