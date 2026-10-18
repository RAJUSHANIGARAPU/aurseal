"""Integrity of one issued invoice and the journal entries it produced.

:func:`check_invoice` returns every broken rule ([] when sound):

* the invoice number is numeric, the status is the expected one (``OPEN`` for a
  fresh invoice) and a document id is present (the customer got a document);
* nothing is paid yet: the remaining amount equals the total;
* total = net + tax, and tax / net equals the tax rate within a tolerance (the
  rate is a parameter, or read from the invoice);
* at least one journal entry exists and every entry balances
  (:func:`journal_balances`);
* date rules are delegated to :func:`oracles.invoice_dates.invoice_date_problems`
  (pass ``dates=False`` to skip them, e.g. for a migrated invoice).

Amounts are read as :class:`~decimal.Decimal` from JSON numbers or strings.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from oracles.invoice_dates import invoice_date_problems

CENT = Decimal("0.01")


@dataclass(frozen=True)
class InvoiceFields:
    number: str = "number"
    status: str = "status"
    document: str = "document_id"
    net: str = "net"
    tax: str = "tax"
    total: str = "total"
    remaining: str = "remaining"
    tax_rate: str = "tax_rate"


def _dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def journal_balances(
    entry: Mapping[str, Any],
    *,
    lines: str = "lines",
    debit: str = "debit",
    credit: str = "credit",
    tolerance: Decimal = CENT,
) -> list[str]:
    """[] when the entry's debits equal its credits; a problem when not or when it is empty."""
    rows = entry.get(lines) or []
    if not rows:
        return [f"journal entry {entry.get('id', '?')} has no lines"]
    debits = sum((_dec(r.get(debit)) or Decimal(0) for r in rows), Decimal(0))
    credits = sum((_dec(r.get(credit)) or Decimal(0) for r in rows), Decimal(0))
    if abs(debits - credits) > tolerance:
        return [f"journal entry {entry.get('id', '?')} debits {debits} != credits {credits}"]
    return []


def check_invoice(
    invoice: Mapping[str, Any],
    journal: Iterable[Mapping[str, Any]],
    *,
    tax_rate: Any = None,
    expected_status: str = "OPEN",
    tolerance: Decimal = CENT,
    rate_tolerance: Decimal = Decimal("0.1"),
    dates: bool = True,
    payment_term_days: int = 30,
    fields: InvoiceFields | None = None,
) -> list[str]:
    """Every broken rule on ``invoice`` and its ``journal`` entries ([] when sound).

    ``rate_tolerance`` is in percentage points: rounding the tax to the cent moves the
    effective rate on a small net amount.
    """
    f = fields or InvoiceFields()
    problems: list[str] = []
    number = str(invoice.get(f.number) or "")
    if not number.isdigit():
        problems.append(f"invoice number {number!r} is not numeric")
    if invoice.get(f.status) != expected_status:
        problems.append(f"status {invoice.get(f.status)!r}, expected {expected_status!r}")
    if invoice.get(f.document) in (None, ""):
        problems.append("no document id: the invoice was never rendered for the customer")
    net, tax, total = (_dec(invoice.get(k)) for k in (f.net, f.tax, f.total))
    remaining = _dec(invoice.get(f.remaining))
    if None in (net, tax, total, remaining):
        problems.append(
            f"missing amount(s): net={net} tax={tax} total={total} remaining={remaining}"
        )
    else:
        if abs(remaining - total) > tolerance:
            problems.append(f"remaining {remaining} != total {total} on an unpaid invoice")
        if abs(net + tax - total) > tolerance:
            problems.append(f"net {net} + tax {tax} = {net + tax}, not total {total}")
        rate = _dec(tax_rate if tax_rate is not None else invoice.get(f.tax_rate))
        if rate is None:
            problems.append("no tax rate to check the tax against")
        elif net != 0:
            effective = tax / net * 100
            if abs(effective - rate) > rate_tolerance:
                problems.append(
                    f"tax {tax} on net {net} is {effective:.2f}%, expected {rate}% "
                    f"(+/- {rate_tolerance})"
                )
    entries = list(journal)
    if not entries:
        problems.append("no journal entry for the invoice")
    for entry in entries:
        problems += journal_balances(entry, tolerance=tolerance)
    if dates:
        problems += invoice_date_problems(invoice, payment_term_days=payment_term_days)
    return problems
