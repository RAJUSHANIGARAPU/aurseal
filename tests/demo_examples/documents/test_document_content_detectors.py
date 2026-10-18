"""Detector pairs for two account-opened letter defects: a contact line printed without a
contact, and the charge per period printed as a bare number."""

from __future__ import annotations

from decimal import Decimal

import pytest

from tests.demo_examples import detector_pair
from tests.demo_examples.documents import oracles
from tests.demo_examples.documents._support import content_fields, document_texts


def opened_letter(demo_control, demo_seed, documents, **fields) -> tuple[dict, str]:
    demo_control.freeze("2026-06-03T09:00:00Z")
    sub = demo_seed.subscription(start_date="2026-06-01", **fields)
    texts = document_texts(documents, sub["id"], ("account_opened", "invoice"))
    return sub, texts


@pytest.mark.parametrize("fault_on", [True, False], ids=["fault-on", "fault-off"])
def test_absent_contact_line_detector(demo_control, demo_seed, documents, fault_on):
    demo_control.faults(*(["doc_optional_prints_none"] if fault_on else []))
    sub, texts = opened_letter(
        demo_control, demo_seed, documents, **content_fields(with_contact=False)
    )
    text = texts["account_opened"]

    def exercised() -> None:
        oracles.assert_document_exercised(text, sub["number"], "Data region:")
        # with a contact, the line shows the name, fault or not
        named, named_texts = opened_letter(
            demo_control, demo_seed, documents, **content_fields(with_contact=True)
        )
        name = named["billing_contact"]["name"]
        oracles.assert_line_value(named_texts["account_opened"], "Billing contact:", name)

    detector_pair.judge(
        demo_control,
        ticket=oracles.CONTACT_DEFECT,
        signature=oracles.CONTACT_SIGNATURE,
        oracle=lambda: oracles.assert_contact_line_absent(text),
        exercised=exercised,
        fault="doc_optional_prints_none",
        fault_on=fault_on,
    )


@pytest.mark.parametrize("fault_on", [True, False], ids=["fault-on", "fault-off"])
def test_period_charge_format_detector(demo_control, demo_seed, documents, fault_on):
    demo_control.faults(*(["doc_amount_unformatted"] if fault_on else []))
    sub, texts = opened_letter(demo_control, demo_seed, documents)
    demo_seed.book_due(sub["id"])
    invoices = document_texts(documents, sub["id"], ("invoice",))
    expected = Decimal(str(sub["price"]["period_gross"])).quantize(Decimal("0.01"))

    def exercised() -> None:
        oracles.assert_document_exercised(texts["account_opened"], sub["number"], "Data region:")
        # the invoice goes through the same renderer and is formatted, fault or not
        oracles.assert_money_line(invoices["invoice"], "Amount due:")

    detector_pair.judge(
        demo_control,
        ticket=oracles.AMOUNT_DEFECT,
        signature=oracles.AMOUNT_SIGNATURE,
        oracle=lambda: oracles.assert_period_charge_formatted(texts["account_opened"], expected),
        exercised=exercised,
        fault="doc_amount_unformatted",
        fault_on=fault_on,
    )
