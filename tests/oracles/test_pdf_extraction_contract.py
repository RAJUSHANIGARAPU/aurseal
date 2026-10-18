"""Extraction contract: what pypdf's layout mode makes of a known rendered document.

Every document oracle judges extracted text, so a library upgrade that changes
layout heuristics changes every verdict at once. These assertions are exact on
purpose: when a pypdf bump breaks them, re-derive them from the new output and
review whether the label/value oracles still hold, instead of loosening them.

The fixture ``fixtures/rendered-invoice.pdf`` (invented content) is written by
``tests/oracles/pdf_fixtures.py``; regenerate it with
``python -m tests.oracles.pdf_fixtures``.
"""

from __future__ import annotations

import pytest

pytest.importorskip("pypdf")

from oracles import document_findings as di  # noqa: E402
from oracles import pdf_text  # noqa: E402
from tests.oracles.pdf_fixtures import INVOICE_FIXTURE, invoice_pdf  # noqa: E402

EXPECTED_LINES = [
    "INVOICE",
    "",
    "",
    "Invoice number                            INV-2031-0042",
    "Invoice date                                 4 May 2031",
    "Customer                                     Example Trading Ltd",
    "",
    "Delivery address",
    "12 Sample Street",
    "Exampletown EX1 2AB",
    "",
    "",
    "Item                                             Quantity                          Amount",
    "Desk lamp                                   2                                     1,250.00",
    "Cable set                                     1                                     48.50",
    "",
    "",
    "Total due                                     GBP 1,298.50",
    "Payment terms",
    "Due within 30 days of the invoice date.",
    "Reference                                    ORD-42",
]

LABELS = ["Invoice number", "Invoice date", "Customer", "Total due", "Reference"]


@pytest.fixture(scope="module")
def text() -> str:
    return pdf_text.extract_text(INVOICE_FIXTURE.read_bytes())


def test_fixture_matches_its_writer():
    assert INVOICE_FIXTURE.read_bytes() == invoice_pdf(), (
        "fixture differs from tests/oracles/pdf_fixtures.py; "
        "regenerate with python -m tests.oracles.pdf_fixtures"
    )


def test_exact_line_structure(text):
    assert text.splitlines() == EXPECTED_LINES


def test_label_and_value_share_a_line(text):
    lines = text.splitlines()
    for label, value in [
        ("Invoice number", "INV-2031-0042"),
        ("Customer", "Example Trading Ltd"),
        ("Total due", "GBP 1,298.50"),
        ("Reference", "ORD-42"),
    ]:
        assert any(label in line and value in line for line in lines), label


def test_block_order_is_reading_order(text):
    order = ["INVOICE", "Invoice number", "Delivery address", "Item", "Total due", "Payment terms"]
    positions = [text.index(marker) for marker in order]
    assert positions == sorted(positions)


def test_no_oracle_fires_on_a_correct_document(text):
    findings = di.all_findings(
        text,
        pinned_text={"customer": "Example Trading Ltd", "total": "1,298.50"},
        must_show=LABELS,
        section_headings=["Delivery address"],
        money_fields=["Total due"],
        locale="en_GB",
    )
    assert findings == [], di.describe("invoice", findings)


def test_amounts_read_in_locale(text):
    found = pdf_text.extract_amounts(INVOICE_FIXTURE, "en_GB")
    with_decimals = [str(a) for a in found if "." in str(a)]
    assert with_decimals == ["1250.00", "48.50", "1298.50"]


def test_non_empty_on_every_page():
    import io

    import pypdf

    reader = pypdf.PdfReader(io.BytesIO(INVOICE_FIXTURE.read_bytes()))
    assert len(reader.pages) == 2
    assert all(p.extract_text(extraction_mode="layout").strip() for p in reader.pages)
