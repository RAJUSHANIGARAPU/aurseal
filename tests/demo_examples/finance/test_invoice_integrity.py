"""Every invoice of a seeded, booked subscription passes the invoice/journal integrity oracle."""

from __future__ import annotations

from oracles.invoice_integrity import check_invoice
from tests.demo_examples.finance._support import BOOK_UNTIL, FROZEN_NOW, START_DATE, process_due


def test_invoice_integrity(demo_control, demo_seed, demo_http, billing):
    demo_control.freeze(FROZEN_NOW)
    sub = demo_seed.subscription(start_date=START_DATE)
    process_due(demo_http, sub["id"], BOOK_UNTIL)
    tax_rate = sub["price"]["tax_rate"]

    invoice, journal = billing.invoice_with_journal(sub["id"])
    assert check_invoice(invoice, journal, tax_rate=tax_rate) == []
    # the fee lands on the first invoice: its total exceeds the plain period price
    assert invoice["total"] > sub["price"]["period_gross"]

    invoices = billing.invoices_of(sub["id"])
    assert len(invoices) == 2
    for other in invoices:
        problems = check_invoice(other, billing.invoice_journal(other["id"]), tax_rate=tax_rate)
        assert problems == [], f"invoice {other['number']}: {problems}"
    # a wrong expected rate is caught, so the clean result above was a real comparison
    assert check_invoice(invoice, journal, tax_rate=9)
