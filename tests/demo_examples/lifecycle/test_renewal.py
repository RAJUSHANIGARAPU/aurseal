"""Renewal outcomes: the booked renewal, backdated and reused subscriptions, indexed prices."""

from __future__ import annotations

from decimal import Decimal as D

from oracles.charges import indexed_amount
from test_data.pool import select_targets
from test_data.seed_mode import resolve
from tests.demo_examples.lifecycle import support


def renew(api, sub_id, **fields):
    draft = support.open_renewal(api, sub_id, **fields)
    return draft, support.confirm_draft(api, draft["id"])


def test_renewal_books_renewal_coded_transaction(demo_http, frozen, seed_renewable_subscription):
    sub_id = seed_renewable_subscription(frequency="YEARLY")["subscription_id"]
    before = support.subscription(demo_http, sub_id)
    _, renewed = renew(demo_http, sub_id)
    booked = support.book_due(demo_http, sub_id, until=before["term_end"])

    (row,) = [t for t in support.transactions(demo_http, sub_id) if t["kind"] == "RENEW"]
    assert row["id"] in booked and row["state"] == "BOOKED"
    assert (row["period_start"], row["period_end"]) == (before["term_end"], renewed["term_end"])


def test_backdated_subscription_renews(demo_http, demo_seed, demo_control):
    demo_control.freeze("2025-03-10T09:00:00Z")  # "a year ago": the subscription starts today
    sub = demo_seed.subscription(start_date="2025-03-10", frequency="YEARLY")
    support.book_due(demo_http, sub["id"])
    demo_control.freeze("2026-02-20T09:00:00Z")  # a year later, inside the renewal window
    _, renewed = renew(demo_http, sub["id"])
    assert renewed["status"] == "renewed"
    assert (renewed["term_start"], renewed["term_end"]) == ("2026-03-10", "2027-03-10")


def test_indexation_applies_to_net_price(demo_http, frozen, seed_renewable_subscription):
    sub_id = seed_renewable_subscription(frequency="QUARTERLY")["subscription_id"]
    before = support.subscription(demo_http, sub_id)["price"]
    draft, renewed = renew(demo_http, sub_id, index_percent=4)
    factor = D(str(draft["preview"]["index_percent"])) / 100
    assert factor == D("0.04")

    after = renewed["price"]
    monthly = indexed_amount(D(str(before["monthly_net"])), factor)
    one_month = support.DEMO_CHARGES.compute(monthly, 1)
    assert D(str(after["monthly_net"])) == monthly
    assert D(str(after["monthly_tax"])) == one_month.tax
    assert D(str(after["monthly_gross"])) == one_month.total
    assert D(str(after["period_net"])) == monthly * 3
    assert D(str(after["period_tax"])) == support.DEMO_CHARGES.compute(monthly * 3, 3).tax
    assert after["tax_rate"] == before["tax_rate"]


def test_reused_subscription_renews_to_confirmed(demo_http, frozen, seed_renewable_subscription):
    for _ in range(2):  # stock the pool the way an earlier run would have
        seed_renewable_subscription()
    targets = select_targets(
        resolve({"SEED_MODE": "reuse"}), support.renewable_pool(demo_http), None, limit=2
    )
    assert targets and not any(t.was_seeded for t in targets)
    for target in targets:
        _, renewed = renew(demo_http, target.entity["id"])
        assert renewed["status"] == "renewed", target.label
