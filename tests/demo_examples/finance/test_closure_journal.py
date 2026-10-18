"""A closure's journal entry balances, and to the same total the closure notice prints."""

from __future__ import annotations

from decimal import Decimal

from oracles.pdf_text import extract_text
from tests.demo_examples.documents.oracles import money_after_label
from tests.demo_examples.finance._support import (
    BOOK_UNTIL,
    CANCEL_EFFECTIVE,
    FROZEN_NOW,
    START_DATE,
    process_due,
)


def test_closure_journal_matches_document_total(demo_control, demo_seed, demo_http, billing):
    demo_control.freeze(FROZEN_NOW)
    sub = demo_seed.subscription(start_date=START_DATE)
    process_due(demo_http, sub["id"], BOOK_UNTIL)
    draft = demo_seed.draft(sub["id"], "cancel", effective_date=CANCEL_EFFECTIVE, reason="OTHER")
    demo_seed.confirm_draft(draft["id"])
    process_due(demo_http, sub["id"], CANCEL_EFFECTIVE)

    (closure_tx,) = [t for t in billing.transactions(sub["id"]) if t["kind"] == "CANCEL"]
    assert closure_tx["state"] == "BOOKED", closure_tx
    (entry,) = billing.journal([closure_tx["reference"]])
    debit = sum(Decimal(str(line["debit"])) for line in entry["lines"])
    credit = sum(Decimal(str(line["credit"])) for line in entry["lines"])
    assert debit == credit, entry["lines"]

    docs = demo_http.get(f"subscriptions/{sub['id']}/documents").raise_for_status().json()
    notice = next(d for d in docs if d["template"] == "closure_notice")
    pdf = demo_http.get(f"documents/{notice['id']}.pdf").raise_for_status().content
    printed = money_after_label(extract_text(pdf), "Credit:")
    assert printed is not None and abs(printed) == debit, (printed, debit)
    assert printed == Decimal(str(closure_tx["total"]))
