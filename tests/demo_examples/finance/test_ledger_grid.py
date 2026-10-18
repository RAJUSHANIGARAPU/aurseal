"""A seeded subscription's ledger grid (deep link ``#ledger``) equals the API ledger.

The detail screen renders its ledger grid on the server, so the example reads the page over
HTTP and parses the grid; no browser is needed for a server-rendered table.
"""

from __future__ import annotations

from tests.demo_examples.finance._support import (
    FROZEN_NOW,
    START_DATE,
    grid_row_of,
    ledger_grid_rows,
    process_due,
)


def test_seeded_subscription_ledger_grid_matches_api(
    demo_control, demo_seed, demo_sut, demo_http, billing
):
    demo_control.freeze(FROZEN_NOW)
    sub = demo_seed.subscription(start_date=START_DATE)
    process_due(demo_http, sub["id"], "2026-03-31")  # pre-billing: first period booked only

    page = demo_sut.client.get(f"/ui/subscriptions/{sub['id']}#ledger")
    assert page.status == 200
    html = page.body.decode()
    assert 'id="ledger"' in html, "the deep-link target is missing"
    grid = ledger_grid_rows(html)
    api = billing.transactions(sub["id"])

    assert len(grid) == len(api) == 12, (len(grid), len(api))
    for row, tx in zip(grid, api, strict=True):
        assert row["row_state"] == row["state"]
        assert {k: row[k] for k in grid_row_of(tx)} == grid_row_of(tx)
    assert [r["state"] for r in grid].count("BOOKED") == 1
