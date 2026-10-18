"""Booking precondition over the API: replay the process fixture, then read the ledger.

Booking due invoices is a prerequisite for cancellation, change and renewal flows. It is done
over the API (a replayed request fixture) rather than through a screen, and this example
asserts it actually happened: due periods BOOKED with an invoice each, later ones untouched.
"""

from __future__ import annotations

from tests.demo_examples.finance._support import BOOK_UNTIL, FROZEN_NOW, START_DATE, process_due


def test_process_due_invoices(demo_control, demo_seed, demo_http, billing):
    demo_control.freeze(FROZEN_NOW)
    sub = demo_seed.subscription(start_date=START_DATE)
    before = billing.transactions(sub["id"])
    assert {t["state"] for t in before} == {"SCHEDULED"}, before

    booked = process_due(demo_http, sub["id"], BOOK_UNTIL)

    after = {t["id"]: t for t in billing.transactions(sub["id"])}
    due = [t for t in after.values() if t["period_start"] <= BOOK_UNTIL]
    later = [t for t in after.values() if t["period_start"] > BOOK_UNTIL]
    assert sorted(booked) == sorted(t["id"] for t in due) and len(due) == 2
    assert {t["state"] for t in due} == {"BOOKED"}
    assert later and {t["state"] for t in later} == {"SCHEDULED"}
    invoiced = {i["transaction_id"] for i in billing.invoices_of(sub["id"])}
    assert invoiced == set(booked)
    # replaying again books nothing new: the precondition is idempotent
    assert process_due(demo_http, sub["id"], BOOK_UNTIL) == []
