"""Live invoices follow the date derivation rules (backdated and future-dated periods)."""

from __future__ import annotations

from oracles.invoice_dates import assert_invoice_dates, expected_reference_date
from tests.demo_examples.finance._support import FROZEN_NOW, process_due

PAYMENT_TERM_DAYS = 14


def test_invoice_dates(demo_control, demo_seed, demo_http, billing):
    demo_control.freeze(FROZEN_NOW)
    demo_control.settings(payment_term_days=PAYMENT_TERM_DAYS)
    # started a month before today: the first period is backdated, the third lies in the future
    sub = demo_seed.subscription(start_date="2026-02-01")
    process_due(demo_http, sub["id"], "2026-04-01")
    txs = {t["id"]: t for t in billing.transactions(sub["id"])}
    invoices = sorted(billing.invoices_of(sub["id"]), key=lambda i: i["period_start"])
    assert [i["period_start"] for i in invoices] == ["2026-02-01", "2026-03-01", "2026-04-01"]
    for invoice in invoices:
        assert_invoice_dates(
            invoice,
            transaction=txs[invoice["transaction_id"]],
            payment_term_days=PAYMENT_TERM_DAYS,
        )
    backdated, _, future = invoices
    assert backdated["reference_date"] == backdated["booking_date"] == FROZEN_NOW[:10]
    assert (
        future["reference_date"]
        == "2026-04-01"
        == str(
            expected_reference_date(future["booking_date"], future["document_date"], "2026-04-01")
        )
    )
