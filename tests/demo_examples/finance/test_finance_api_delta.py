"""Promotion-check style API checks on the finance surface: managed flag, statement export."""

from __future__ import annotations

from decimal import Decimal

import pytest

from tests.demo_examples.finance._support import (
    BOOK_UNTIL,
    CANCEL_EFFECTIVE,
    FROZEN_NOW,
    START_DATE,
    process_due,
)


def test_accounts_carry_managed_flag(billing):
    accounts = billing.accounts()
    assert accounts
    assert [a["number"] for a in accounts if not isinstance(a.get("managed"), bool)] == []
    mapped = set(billing.ledger_codes().values())
    assert mapped and all(a["managed"] for a in accounts if a["number"] in mapped)
    assert any(not a["managed"] for a in accounts), "no unmanaged account to contrast with"


@pytest.fixture
def partner_with_booked_and_cancelled(demo_control, demo_seed, demo_http):
    demo_control.freeze(FROZEN_NOW)
    partner = demo_seed.partner(reseller_terms_id=1)
    sub = demo_seed.subscription(start_date=START_DATE, partner_id=partner["id"])
    process_due(demo_http, sub["id"], BOOK_UNTIL)
    draft = demo_seed.draft(sub["id"], "cancel", effective_date=CANCEL_EFFECTIVE, reason="BUDGET")
    demo_seed.confirm_draft(draft["id"])
    process_due(demo_http, sub["id"], CANCEL_EFFECTIVE)
    return partner


@pytest.mark.parametrize(
    ("window", "side"),
    [(("2026-03-01", "2026-05-01"), "CREDIT"), ((CANCEL_EFFECTIVE, "2026-04-21"), "DEBIT")],
    ids=["positive-balance", "negative-balance"],
)
def test_statement_export_single_entry_signed(
    window, side, partner_with_booked_and_cancelled, billing
):
    partner = partner_with_booked_and_cancelled
    settlement = billing.create_settlement(
        {"partner_id": partner["id"], "period_start": window[0], "period_end": window[1]}
    )
    balance = Decimal(str(settlement["closing_balance"]))
    assert balance != 0, settlement
    response = billing.statement(settlement["id"])
    assert response.status == 200, response
    export = response.json()
    assert len(export["entries"]) == 1, export
    entry = export["entries"][0]
    assert Decimal(str(entry["amount"])) == abs(balance)
    assert entry["side"] == side == ("CREDIT" if balance > 0 else "DEBIT")
    assert entry["reference"] == settlement["reference"] == export["settlement"]
    assert entry["party"] == partner["number"]


def test_unknown_settlement_is_refused_naming_the_id(billing):
    response = billing.statement(987654)
    assert response.status == 404, response
    assert "987654" in response.text
