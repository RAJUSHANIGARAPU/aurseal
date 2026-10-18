"""The ledger grid shows exactly the schedule the confirm call answered with.

The confirm response is captured (it carries the scheduled transactions); the detail
screen's ledger grid is then compared row by row with it, and the rendered rows are checked
structurally with :mod:`oracles.ledger_checks`. When the response carries no schedule, there
is nothing to compare: the example records an abstention instead of passing.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from oracles import ledger_checks
from tests.demo_examples.finance._support import (
    FROZEN_NOW,
    START_DATE,
    grid_row_of,
    ledger_grid_rows,
)
from verification.evidence_verdict import EvidenceVerdict


def test_invoice_grid_matches_api(demo_control, demo_seed, demo_sut, demo_http):
    demo_control.freeze(FROZEN_NOW)
    sub = demo_seed.subscription(start_date=START_DATE, state="pending_payment")
    captured = demo_http.post(f"subscriptions/{sub['id']}/actions/confirm").raise_for_status()
    schedule = captured.json().get("scheduled_transactions")
    coverage = EvidenceVerdict(f"ledger grid vs confirm schedule of {sub['number']}")
    if not schedule:
        coverage.no_verdict("the confirm response carried no scheduled transactions")

    grid = ledger_grid_rows(demo_sut.client.get(f"/ui/subscriptions/{sub['id']}").body.decode())
    assert [{k: r[k] for k in grid_row_of(t)} for r, t in zip(grid, schedule, strict=False)] == [
        grid_row_of(t) for t in schedule
    ]
    assert len(grid) == len(schedule)

    rows = [
        {
            "start": date.fromisoformat(r["period_start"]),
            "end": date.fromisoformat(r["period_end"]) - timedelta(days=1),
            "net": Decimal(r["net"]),
            "tax": Decimal(r["tax"]),
            "fee": Decimal(r["fee"]),
            "total": Decimal(r["total"]),
            "kind": r["kind"],
            "state": r["state"],
        }
        for r in grid
    ]
    # the grid shows the fee net only, so the full identity is checked on the captured schedule
    problems = [
        *ledger_checks.additive_identity(schedule, ["net", "tax", "fee_net", "fee_tax"], "total"),
        *ledger_checks.contiguous(rows),
        *ledger_checks.steps_match_term(rows, 1),
        *ledger_checks.allowed_values(rows, "state", {"SCHEDULED"}),
        *ledger_checks.sequence(rows, "kind", "NEW", "NEW"),
    ]
    assert problems == []
    assert rows[0]["fee"] > 0 and all(r["fee"] == 0 for r in rows[1:]), "fee on the first period"
    coverage.proved(f"{len(rows)} grid rows equal the confirm schedule of {sub['number']}")
