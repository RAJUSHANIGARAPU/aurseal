"""Date derivation rules for an invoice, as pure functions plus one checker.

An invoice for a backdated or future-dated change carries several dates that are
derived, not entered, and each derivation has a classic mistake:

* **reference date** - the latest of the booking date, the document date and the
  start of the billed period. A backdated change must not carry a reference date
  in the past (before it was booked); a future-dated one must not be referenced
  before its period starts.
* **due date** - the document date plus the payment term (default 30 days), not
  the booking date plus the term and not the period start plus the term.
* **collection date** - defaults to the reference date when no hint was given.
* **invoice period** - equals the period of the transaction it bills.

:func:`invoice_date_problems` returns every broken rule (``[]`` when sound) and
:func:`assert_invoice_dates` raises with all of them at once.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

PAYMENT_DUE_DAYS_DEFAULT = 30


@dataclass(frozen=True)
class DateFields:
    """Field names on the invoice (and on the transaction for the period)."""

    booking: str = "booking_date"
    document: str = "document_date"
    reference: str = "reference_date"
    due: str = "due_date"
    collection: str = "collection_date"
    period_start: str = "period_start"
    period_end: str = "period_end"


def _day(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def expected_reference_date(booking_date: Any, document_date: Any, period_start: Any) -> date:
    """The latest of booking date, document date and period start."""
    return max(_day(booking_date), _day(document_date), _day(period_start))


def expected_due_date(
    document_date: Any, payment_term_days: int = PAYMENT_DUE_DAYS_DEFAULT
) -> date:
    return _day(document_date) + timedelta(days=payment_term_days)


def invoice_date_problems(
    invoice: Mapping[str, Any],
    *,
    transaction: Mapping[str, Any] | None = None,
    payment_term_days: int = PAYMENT_DUE_DAYS_DEFAULT,
    collection_hint: Any = None,
    fields: DateFields | None = None,
) -> list[str]:
    """Every broken date rule on ``invoice`` ([] when all hold)."""
    f = fields or DateFields()
    missing = [
        name
        for name in (f.booking, f.document, f.reference, f.due, f.period_start)
        if invoice.get(name) in (None, "")
    ]
    if missing:
        return [f"invoice is missing date field(s) {missing}"]
    problems = []
    reference = _day(invoice[f.reference])
    want_ref = expected_reference_date(
        invoice[f.booking], invoice[f.document], invoice[f.period_start]
    )
    if reference != want_ref:
        problems.append(
            f"reference date {reference} is not the latest of booking {invoice[f.booking]}, "
            f"document {invoice[f.document]} and period start {invoice[f.period_start]} "
            f"({want_ref})"
        )
    want_due = expected_due_date(invoice[f.document], payment_term_days)
    if _day(invoice[f.due]) != want_due:
        problems.append(
            f"due date {invoice[f.due]} is not document date {invoice[f.document]} + "
            f"{payment_term_days} days ({want_due})"
        )
    collection = invoice.get(f.collection)
    want_collection = _day(collection_hint) if collection_hint else want_ref
    if collection in (None, "") or _day(collection) != want_collection:
        source = "the hint" if collection_hint else "the reference date"
        problems.append(f"collection date {collection} is not {source} ({want_collection})")
    if transaction is not None:
        for name in (f.period_start, f.period_end):
            if str(invoice.get(name)) != str(transaction.get(name)):
                problems.append(
                    f"invoice {name} {invoice.get(name)} differs from the transaction's "
                    f"{transaction.get(name)}"
                )
    return problems


def assert_invoice_dates(invoice: Mapping[str, Any], **kwargs: Any) -> None:
    """Raise ``AssertionError`` listing every broken rule (:func:`invoice_date_problems`)."""
    problems = invoice_date_problems(invoice, **kwargs)
    if problems:
        raise AssertionError("invoice dates: " + "; ".join(problems))
