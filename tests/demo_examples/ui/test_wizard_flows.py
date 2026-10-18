"""Full wizard runs with prerequisites provisioned over the API and picked by number."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from tests.demo_examples.ui import support
from ui.flows.demo_subscription_flow import SubscriptionWizardFlow, WizardInput
from ui.pages.demo.draft_screen import DraftScreen
from ui.pages.demo.subscription_detail import SubscriptionDetail

pytestmark = pytest.mark.demo_ui


def test_individual_plan_create_confirm_cancel_via_ui(demo_page, demo_sut, demo_control, demo_seed):
    demo_control.freeze(support.NOW)
    client = demo_sut.client
    partner = demo_seed.partner()
    customer = support.segment_customer(demo_seed, "individual")
    run = SubscriptionWizardFlow(demo_page, demo_sut.base_url).create(
        WizardInput(
            partner["number"],
            customer["number"],
            "Individual plan (INDIVIDUAL)",
            support.date_of("2026-03-01"),
            workspace_type="SANDBOX",
        )
    )
    sub_id = run.subscription_id
    confirmed = support.subscription(client, sub_id)
    assert confirmed["status"] == "active" and confirmed["plan_kind"] == "INDIVIDUAL"
    news = support.transactions(client, sub_id, "NEW")
    assert len(news) == 12 and news[0]["fee_net"] > 0 and all(t["net"] > 0 for t in news)
    assert support.money(news[1]["net"]) == Decimal("9.75")
    demo_seed.book_due(sub_id)

    detail = SubscriptionDetail(demo_page, demo_sut.base_url)
    detail.wait_loaded()
    started = detail.start("cancel")
    assert started.status == 201 and started.advanced
    draft = DraftScreen(demo_page, demo_sut.base_url)
    draft.wait_ready()
    draft.set_effective_date(support.date_of("2026-03-16"))
    draft.choose_reason("BUDGET")
    draft.tick_precondition()
    assert draft.confirm("confirm_cancel").outcome.status == 200

    assert support.subscription(client, sub_id)["status"] == "cancelled"
    (cancel,) = support.transactions(client, sub_id, "CANCEL")
    assert cancel["net"] < 0
    voided = [t for t in support.transactions(client, sub_id, "NEW") if t["state"] == "VOID"]
    assert len(voided) == 11, "periods after the cancellation must be voided"


def test_team_plan_create_to_confirmed_via_ui(demo_page, demo_sut, demo_control, demo_seed):
    """Partner, plan and customer provisioned over the API, read back, selected in the UI."""
    demo_control.freeze(support.NOW)
    client = demo_sut.client
    code = f"TEAM-{uuid.uuid4().hex[:6].upper()}"
    plan = client.ok(
        "POST",
        "/api/plans",
        {"code": code, "name": f"Team {code}", "kind": "TEAM", "base_monthly_net": 47.5},
    )
    partner = client.ok("GET", f"/api/partners/{demo_seed.partner()['id']}")
    customer = client.ok(
        "GET", f"/api/customers/{support.segment_customer(demo_seed, 'company')['id']}"
    )
    run = SubscriptionWizardFlow(demo_page, demo_sut.base_url).create(
        WizardInput(
            partner["number"],
            customer["number"],
            f"{plan['name']} (TEAM)",
            support.date_of("2026-03-01"),
            frequency="Quarterly",
        )
    )
    sub = support.subscription(client, run.subscription_id)
    assert sub["status"] == "active"
    assert sub["plan_id"] == plan["id"] and sub["frequency"] == "QUARTERLY"
    assert sub["partner"]["number"] == partner["number"]
    assert sub["holder"]["number"] == customer["number"]
    assert support.money(sub["price"]["monthly_net"]) == Decimal("47.50")
    assert len(support.transactions(client, run.subscription_id, "NEW")) == 4
