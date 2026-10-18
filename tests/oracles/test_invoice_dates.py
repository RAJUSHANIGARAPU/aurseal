"""Table-driven tests of oracles.invoice_dates over eight invented use cases."""

from __future__ import annotations

from datetime import date

import pytest

from oracles import invoice_dates as idates


def _invoice(booking, document, period_start, *, term=30, collection=None, **over):
    ref = idates.expected_reference_date(booking, document, period_start)
    inv = {
        "booking_date": booking,
        "document_date": document,
        "period_start": period_start,
        "period_end": "2026-12-31",
        "reference_date": ref.isoformat(),
        "due_date": idates.expected_due_date(document, term).isoformat(),
        "collection_date": collection or ref.isoformat(),
    }
    inv.update(over)
    return inv


# (case, booking, document, period start, expected reference date)
CASES = [
    (
        "new subscription, period starts on booking day",
        "2026-03-10",
        "2026-03-10",
        "2026-03-10",
        "2026-03-10",
    ),
    (
        "backdated change, period already started",
        "2026-03-10",
        "2026-03-10",
        "2026-02-01",
        "2026-03-10",
    ),
    (
        "future-dated change, period not yet started",
        "2026-03-10",
        "2026-03-10",
        "2026-05-01",
        "2026-05-01",
    ),
    (
        "mid-period change booked the same day",
        "2026-03-20",
        "2026-03-20",
        "2026-03-20",
        "2026-03-20",
    ),
    ("document re-issued after booking", "2026-03-10", "2026-03-15", "2026-03-01", "2026-03-15"),
    ("backdated cancellation", "2026-04-02", "2026-04-02", "2026-03-15", "2026-04-02"),
    ("future-dated renewal", "2026-10-01", "2026-10-01", "2027-01-01", "2027-01-01"),
    ("change across a year boundary", "2026-12-28", "2026-12-28", "2027-01-01", "2027-01-01"),
]


@pytest.mark.parametrize(
    ("case", "booking", "document", "start", "want"), CASES, ids=[c[0] for c in CASES]
)
def test_reference_date_is_the_latest_of_three(case, booking, document, start, want):
    assert idates.expected_reference_date(booking, document, start) == date.fromisoformat(want)
    assert idates.invoice_date_problems(_invoice(booking, document, start)) == []


def test_due_date_is_document_date_plus_term():
    assert idates.expected_due_date("2026-03-10") == date(2026, 4, 9)
    assert idates.expected_due_date(date(2026, 3, 10), 14) == date(2026, 3, 24)
    inv = _invoice("2026-03-10", "2026-03-15", "2026-03-01", term=14)
    assert idates.invoice_date_problems(inv, payment_term_days=14) == []
    problems = idates.invoice_date_problems(inv)
    assert len(problems) == 1 and "due date 2026-03-29" in problems[0]


def test_reference_date_in_the_past_is_reported():
    inv = _invoice("2026-03-10", "2026-03-10", "2026-02-01", reference_date="2026-02-01")
    problems = idates.invoice_date_problems(inv)
    assert any("reference date 2026-02-01 is not the latest" in p for p in problems)


def test_collection_defaults_to_reference_unless_hinted():
    inv = _invoice("2026-03-10", "2026-03-10", "2026-05-01", collection="2026-03-10")
    assert any("collection date" in p for p in idates.invoice_date_problems(inv))
    assert idates.invoice_date_problems(inv, collection_hint="2026-03-10") == []


def test_invoice_period_must_equal_the_transaction_period():
    inv = _invoice("2026-03-10", "2026-03-10", "2026-03-01")
    tx = {"period_start": "2026-03-01", "period_end": "2026-12-31"}
    assert idates.invoice_date_problems(inv, transaction=tx) == []
    tx["period_end"] = "2026-04-01"
    assert any("period_end" in p for p in idates.invoice_date_problems(inv, transaction=tx))


def test_missing_dates_are_reported_not_guessed():
    assert idates.invoice_date_problems({"booking_date": "2026-03-10"})[0].startswith(
        "invoice is missing date field(s)"
    )


def test_assert_invoice_dates_raises_with_every_problem():
    inv = _invoice(
        "2026-03-10", "2026-03-10", "2026-05-01", due_date="2026-01-01", collection_date="x"
    )
    with pytest.raises(AssertionError, match="invoice dates: .*due date.*collection date"):
        idates.assert_invoice_dates({**inv, "collection_date": "2026-01-01"})
    idates.assert_invoice_dates(_invoice("2026-03-10", "2026-03-10", "2026-03-10"))
