"""From parties to a confirmed subscription in the wizard, and the ledger grid it produces."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from oracles import ledger_checks
from tests.demo_examples.ui import support
from ui.flows.demo_subscription_flow import SubscriptionWizardFlow, WizardInput
from ui.pages.demo.subscription_detail import SubscriptionDetail

pytestmark = pytest.mark.demo_ui


def _company_input(demo_seed, *, verified: bool) -> tuple[WizardInput, dict, dict]:
    partner = demo_seed.partner()
    customer = support.segment_customer(demo_seed, "company", verified=verified)
    data = WizardInput(
        partner_number=partner["number"],
        customer_number=customer["number"],
        plan_label="Team plan (TEAM)",
        start=support.date_of("2026-03-01"),
        workspace_type="TRAINING",
        data_region="EU-NORTH",
        net="45.00",
        tax_rate="20",
        commission="10",
    )
    return data, partner, customer


def test_company_quote_to_confirmed(demo_page, demo_sut, demo_control, demo_seed):
    """Parties (with screening) -> price -> quote -> confirm, checked against the API."""
    demo_control.freeze(support.NOW)
    data, partner, customer = _company_input(demo_seed, verified=False)

    run = SubscriptionWizardFlow(demo_page, demo_sut.base_url).create(data)

    assert run.screening_status == "verified", "the unverified customer was not screened"
    assert run.outcomes["parties"].status == 201
    calc = run.outcomes["calculate"]
    assert calc.status == 200 and not calc.rejected, calc.describe()
    quote = calc.body_json()
    assert run.amounts == {
        "net": Decimal("45.00"),
        "tax": Decimal("9.00"),
        "gross": Decimal("54.00"),
        "commission": Decimal("4.50"),
    }
    assert support.money(quote["gross_amount"]) == run.amounts["gross"]
    assert run.outcomes["quote"].status == 200
    assert run.outcomes["confirm"].status == 200

    sub = support.subscription(demo_sut.client, run.subscription_id)
    assert sub["status"] == "active"
    assert sub["partner"]["number"] == partner["number"]
    assert sub["holder"]["number"] == customer["number"]
    assert (sub["workspace_type"], sub["data_region"]) == ("TRAINING", "EU-NORTH")
    assert support.money(sub["price"]["monthly_net"]) == Decimal("45.00")
    assert sub["commission_percentage"] == 10.0
    detail = SubscriptionDetail(demo_page, demo_sut.base_url)
    detail.wait_loaded()
    assert detail.status() == "active" and detail.field("holder-type") == "COMPANY"


def _assert_ledger_structure(detail: SubscriptionDetail, api_rows: list[dict]) -> list[dict]:
    grid = detail.ledger()
    assert grid, "the ledger grid is empty"
    assert len(grid) == len(api_rows)
    for shown, stored in zip(grid, api_rows, strict=True):
        assert shown["kind"] == stored["kind"] and shown["state"] == stored["state"]
        assert shown["period_start"] == stored["period_start"]
        for column, key in (("net", "net"), ("tax", "tax"), ("fee", "fee_net"), ("total", "total")):
            assert shown[column] == support.money(stored[key]), (column, shown, stored)
    news = [row for row in grid if row["kind"] == "NEW"]
    assert grid[0]["kind"] == "NEW"
    assert all(row["total"] > 0 and row["state"] for row in news)
    no_fee = [r for r in news if r["fee"] == 0]
    assert ledger_checks.additive_identity(no_fee, ["net", "tax"], "total") == []
    periods = [
        {
            "start": support.date_of(r["period_start"]),
            "end": support.date_of(r["period_end"]) - timedelta(days=1),
        }
        for r in news
    ]
    assert ledger_checks.contiguous(periods) == []
    assert ledger_checks.steps_match_term(periods, 1) == []
    return grid


@pytest.mark.parametrize("entry", ["fresh", "deep-link"])
def test_ledger_grid_structure(demo_page, demo_sut, demo_control, demo_seed, entry):
    """The grid on the detail page, reached through the wizard or by URL, matches the API."""
    demo_control.freeze(support.NOW)
    if entry == "fresh":
        data, _, _ = _company_input(demo_seed, verified=True)
        sub_id = SubscriptionWizardFlow(demo_page, demo_sut.base_url).create(data).subscription_id
        detail = SubscriptionDetail(demo_page, demo_sut.base_url)
        detail.wait_loaded()
    else:
        sub_id = support.booked(demo_seed)["id"]
        detail = SubscriptionDetail(demo_page, demo_sut.base_url).open(sub_id)

    api_rows = support.transactions(demo_sut.client, sub_id)
    grid = _assert_ledger_structure(detail, api_rows)
    gross = support.subscription(demo_sut.client, sub_id)["price"]["period_gross"]
    total = sum((row["total"] for row in grid), Decimal(0))
    assert total >= Decimal(str(gross)) * 12 / 2, f"grid total {total} is under half a year's gross"
