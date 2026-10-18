"""Offline self-tests of the document oracles: each catches its problem and tells the near
misses apart."""

from __future__ import annotations

from decimal import Decimal

import pytest

from tests.demo_examples.documents import oracles

GOOD = """Account opened for Odile Ledger
Subscription: SUB-004512
Charge per period: EUR 117.00
Details
Data region: AP-SOUTH
Workspace: TRAINING
"""


def test_an_absent_conditional_line_passes_and_a_leaked_null_fails():
    oracles.assert_line_absent(GOOD, "Billing contact:")
    leaked = GOOD + "Billing contact: None\n"
    with pytest.raises(AssertionError, match="no value but is printed anyway"):
        oracles.assert_line_absent(leaked, "Billing contact:")
    shown = GOOD + "Billing contact: Odile Ledger\n"
    with pytest.raises(AssertionError, match="should be absent but shows"):
        oracles.assert_line_absent(shown, "Billing contact:")


def test_the_absence_check_reads_only_the_details_section():
    heading_only = "Billing contact: None\n" + GOOD
    oracles.assert_line_absent(heading_only, "Billing contact:")
    with pytest.raises(AssertionError):
        oracles.assert_line_absent(heading_only, "Billing contact:", section=False)


def test_a_line_value_separates_missing_from_wrong():
    oracles.assert_line_value(GOOD, "Data region:", "AP-SOUTH")
    with pytest.raises(AssertionError, match="a wrong value"):
        oracles.assert_line_value(GOOD, "Data region:", "EU-NORTH")
    with pytest.raises(AssertionError, match="no 'PO reference:' line"):
        oracles.assert_line_value(GOOD, "PO reference:", "PO-1")


@pytest.mark.parametrize(
    ("line", "match"),
    [
        ("Charge per period: 117.0", "not printed as money"),
        ("Charge per period: EUR 117", "not printed as money"),
        ("Charge per period: EUR 116.00", "shows 116.00"),
    ],
)
def test_the_money_line_tells_format_from_amount(line, match):
    oracles.assert_money_line(GOOD, "Charge per period:", Decimal("117.00"))
    with pytest.raises(AssertionError, match=match):
        oracles.assert_money_line(line, "Charge per period:", Decimal("117.00"))


def test_the_control_rules_out_empty_and_foreign_documents():
    oracles.assert_document_exercised(GOOD, "SUB-004512", "Data region:")
    for text, number in (("", "SUB-004512"), (GOOD, "SUB-000001")):
        with pytest.raises(AssertionError):
            oracles.assert_document_exercised(text, number, "Data region:")
    with pytest.raises(AssertionError, match="no Details section"):
        oracles.assert_document_exercised("Subscription: SUB-004512", "SUB-004512", "x")


def test_assert_all_reports_every_finding():
    def bad(message):
        def check():
            raise AssertionError(message)

        return check

    with pytest.raises(AssertionError) as caught:
        oracles.assert_all({"one": bad("first"), "two": lambda: None, "three": bad("third")})
    assert str(caught.value).splitlines() == ["one: first", "three: third"]


def test_money_helpers():
    text = "Adjustment: EUR -4.00 on 2026-06-11\nInvoice 200317"
    assert oracles.money_amounts(text) == [Decimal("-4.00")]
    assert oracles.money_after_label(text, "Adjustment:") == Decimal("-4.00")
    assert oracles.money_after_label(text, "Missing:") is None
    response = {"gross_amount": 39.0, "net_amount": 32.5, "flags": {"ok": True}}
    assert oracles.matching_leaf(response, Decimal("39.00")) == "gross_amount"
    with pytest.raises(AssertionError, match="matches no numeric field"):
        oracles.matching_leaf(response, Decimal("1.00"))


def test_the_tracked_oracles_carry_their_signatures_and_not_on_near_misses():
    oracles.assert_contact_line_absent(GOOD)
    with pytest.raises(AssertionError, match=oracles.CONTACT_SIGNATURE):
        oracles.assert_contact_line_absent(GOOD + "Billing contact: None\n")
    with pytest.raises(AssertionError) as caught:
        oracles.assert_contact_line_absent(GOOD + "Billing contact: Odile Ledger\n")
    assert oracles.CONTACT_DEFECT not in str(caught.value)

    oracles.assert_period_charge_formatted(GOOD, Decimal("117.00"))
    with pytest.raises(AssertionError, match=oracles.AMOUNT_SIGNATURE):
        oracles.assert_period_charge_formatted("Charge per period: 117.0")
    with pytest.raises(AssertionError) as caught:
        oracles.assert_period_charge_formatted(GOOD, Decimal("118.00"))
    assert oracles.AMOUNT_DEFECT not in str(caught.value)
