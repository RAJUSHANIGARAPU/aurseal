"""Tests for oracles.invoice_integrity over invented invoice and journal JSON."""

from __future__ import annotations

from decimal import Decimal

from oracles.invoice_integrity import InvoiceFields, check_invoice, journal_balances

INVOICE = {
    "id": 1,
    "number": "100001",
    "status": "OPEN",
    "document_id": 3,
    "net": 55.0,
    "tax": 11.0,
    "total": 66.0,
    "remaining": 66.0,
    "tax_rate": 20.0,
    "booking_date": "2026-03-10",
    "document_date": "2026-03-10",
    "reference_date": "2026-03-10",
    "due_date": "2026-04-09",
    "collection_date": "2026-03-10",
    "period_start": "2026-03-01",
    "period_end": "2026-04-01",
}
ENTRY = {
    "id": 7,
    "lines": [
        {"account": "1300", "debit": 66.0, "credit": 0.0},
        {"account": "8000", "debit": 0.0, "credit": 40.0},
        {"account": "8100", "debit": 0.0, "credit": 15.0},
        {"account": "1500", "debit": 0.0, "credit": 11.0},
    ],
}


def test_a_sound_invoice_has_no_problems():
    assert check_invoice(INVOICE, [ENTRY]) == []


def test_each_rule_reports_by_name():
    broken = {
        **INVOICE,
        "number": "INV-1",
        "status": "PAID",
        "document_id": None,
        "remaining": 10.0,
        "total": 70.0,
    }
    problems = "\n".join(check_invoice(broken, [ENTRY], dates=False))
    for words in (
        "not numeric",
        "status 'PAID'",
        "no document id",
        "remaining 10.0",
        "not total 70",
    ):
        assert words in problems


def test_tax_rate_is_a_parameter_with_tolerance():
    assert check_invoice(INVOICE, [ENTRY], tax_rate=20) == []
    problems = check_invoice(INVOICE, [ENTRY], tax_rate=9)
    assert len(problems) == 1 and "expected 9%" in problems[0]
    assert check_invoice({**INVOICE, "tax_rate": None}, [ENTRY]) == [
        "no tax rate to check the tax against"
    ]


def test_journal_must_exist_and_balance():
    assert check_invoice(INVOICE, []) == ["no journal entry for the invoice"]
    unbalanced = {"id": 8, "lines": [{"debit": 66.0, "credit": 0}, {"debit": 0, "credit": 60.0}]}
    assert journal_balances(unbalanced) == ["journal entry 8 debits 66.0 != credits 60.0"]
    assert journal_balances({"id": 9, "lines": []}) == ["journal entry 9 has no lines"]
    assert journal_balances(ENTRY, tolerance=Decimal("0")) == []


def test_dates_are_delegated():
    problems = check_invoice({**INVOICE, "due_date": "2026-05-01"}, [ENTRY])
    assert len(problems) == 1 and "due date 2026-05-01" in problems[0]
    assert check_invoice({**INVOICE, "due_date": "2026-05-01"}, [ENTRY], dates=False) == []


def test_missing_amounts_and_custom_fields():
    assert "missing amount(s)" in check_invoice({**INVOICE, "net": None}, [ENTRY])[0]
    fields = InvoiceFields(total="grossAmount", remaining="openAmount")
    renamed = {k: v for k, v in INVOICE.items() if k not in ("total", "remaining")}
    renamed.update(grossAmount=66.0, openAmount=66.0)
    assert check_invoice(renamed, [ENTRY], fields=fields) == []
