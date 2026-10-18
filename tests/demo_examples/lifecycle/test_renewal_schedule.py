"""The schedule after a renewal: where the renewal row starts and what it collects."""

from __future__ import annotations

from datetime import date
from decimal import Decimal as D

from oracles import ledger_checks as lc
from oracles.charges import indexed_amount
from tests.demo_examples.lifecycle import support


def test_renewal_row_starts_on_old_term_end_and_collects_indexed_amount(
    demo_http, frozen, seed_renewable_subscription
):
    seeded = seed_renewable_subscription(frequency="YEARLY")
    sub_id = seeded["subscription_id"]
    before = support.subscription(demo_http, sub_id)
    base = D(str(before["price"]["monthly_net"]))
    draft = support.open_renewal(demo_http, sub_id)
    factor = D(str(draft["preview"]["index_percent"])) / 100
    support.confirm_draft(demo_http, draft["id"])

    live = [t for t in support.transactions(demo_http, sub_id) if t["state"] != "VOID"]
    rows = support.as_rows(live)
    renew = next(r for r in rows if r["kind"] == "RENEW")
    problems = [
        *lc.renewal_row_starts_on(rows, date.fromisoformat(before["term_end"])),
        *lc.contiguous(rows),
        *lc.additive_identity(rows, ["net", "tax", "fee_net", "fee_tax"], "total"),
        *lc.collects_indexed_amount(renew, indexed_amount(base, factor) * 12, base * 12),
    ]
    assert problems == []
