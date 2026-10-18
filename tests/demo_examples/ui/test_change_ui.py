"""Plan changes driven through the screens, and the rule for resending a submit."""

from __future__ import annotations

import json
from decimal import ROUND_HALF_UP, Decimal

import pytest

from tests.demo_examples.ui import support
from ui.components.field_reader import field_is_read_only, read_field
from ui.flows.demo_subscription_flow import SubscriptionWizardFlow, WizardInput, WizardRun
from ui.pages.demo.draft_screen import DraftScreen
from ui.pages.demo.subscription_detail import SubscriptionDetail

pytestmark = pytest.mark.demo_ui


def deep_linked_change(page, demo_sut, demo_seed, segment="company", **seed_fields):
    sub = support.booked(demo_seed, segment, **seed_fields)
    rest_draft = demo_seed.draft(sub["id"], "change")
    draft = DraftScreen(page, demo_sut.base_url).open(rest_draft["id"])
    assert draft.kind() == "change" and draft.steps() == ["details", "price"]
    return sub, draft


def test_a_future_plan_change_on_a_deep_linked_draft(demo_page, demo_sut, demo_control, demo_seed):
    """A raise from a future date: a scheduled version and a pro-rata CHANGE row."""
    demo_control.freeze(support.NOW)
    sub, draft = deep_linked_change(demo_page, demo_sut, demo_seed)
    assert draft.set_effective_date(support.date_of("2026-04-15")) == "2026-04-15"
    old = support.money(sub["price"]["monthly_net"])
    draft.set_monthly_net(str(old + Decimal("4.50")))
    draft.set_addon("audit_trail", True)
    result = draft.confirm("confirm_change")
    assert result.outcome.status == 200 and result.outcome.advanced, result.outcome.describe()

    detail = SubscriptionDetail(demo_page, demo_sut.base_url)
    detail.wait_loaded()
    assert detail.field("version-number") == "1", "a future change must not be current yet"
    future = support.subscription(demo_sut.client, sub["id"], version=2)
    assert future["version_status"] == "scheduled" and future["effective_from"] == "2026-04-15"
    assert future["addons"]["audit_trail"] is True

    (change,) = support.transactions(demo_sut.client, sub["id"], "CHANGE")
    # +4.50 a month from 15 April: 16 of April's 30 days
    expected = (Decimal("4.50") * 16 / 30).quantize(Decimal("0.01"), ROUND_HALF_UP)
    assert change["period_start"] == "2026-04-15"
    assert support.money(change["net"]) == expected
    grid = [row for row in detail.ledger() if row["kind"] == "CHANGE"]
    assert [row["net"] for row in grid] == [expected]


def test_region_change_and_the_safe_retry_rule(demo_page, demo_sut, demo_control, demo_seed):
    demo_control.freeze(support.NOW)
    client = demo_sut.client

    # 1. the change itself: where the workspace's data lives
    sub, draft = deep_linked_change(demo_page, demo_sut, demo_seed, data_region="EU-CENTRAL")
    draft.choose_data_region("AP-SOUTH")
    result = draft.confirm("confirm_change")
    assert result.sent and result.outcome.status == 200, result.outcome.describe()
    assert support.latest(client, sub["id"])["data_region"] == "AP-SOUTH"
    shown = SubscriptionDetail(demo_page, demo_sut.base_url).open(sub["id"])
    assert shown.field("data-region") == "AP-SOUTH"

    # 2. a refused create (429: throttled, nothing stored) is resent: one subscription results
    demo_control.fault("create_throttled_every_other")
    partner = demo_seed.partner()
    customer = support.segment_customer(demo_seed, "company")
    data = WizardInput(
        partner["number"], customer["number"], "Team plan (TEAM)", support.date_of("2026-03-01")
    )
    flow = SubscriptionWizardFlow(demo_page, demo_sut.base_url)
    refusals = 0
    for _ in range(2):  # every second create is throttled: two runs always meet one refusal
        flow.prepare_parties(data, WizardRun())
        first = flow.wizard.submit_parties_tracked()
        if not first.outcome.advanced:
            assert first.outcome.status == 429 and first.may_resend, first.outcome.describe()
            refusals += 1
            again = flow.wizard.submit_parties_tracked()
            assert again.outcome.status == 201 and again.outcome.advanced
    assert refusals >= 1, "nothing was throttled; the resend branch did not run"
    created = client.ok(
        "POST", "/api/search", {"resource": "subscriptions", "text": customer["display_name"]}
    )
    assert created["total"] == 2, f"{created['total']} subscriptions for 2 wizard runs"
    demo_control.fault("create_throttled_every_other", on=False)

    # 3. an answer that may have acted (stored, but the gateway said 504) is NOT resent
    def gateway_timeout(route):
        if route.request.method != "POST":
            route.fallback()
            return
        route.fetch()  # the service receives and stores it
        route.fulfill(status=504, body=json.dumps({"error": "gateway_timeout"}))

    other = support.segment_customer(demo_seed, "company")
    flow.prepare_parties(
        WizardInput(
            partner["number"], other["number"], "Team plan (TEAM)", support.date_of("2026-03-01")
        ),
        WizardRun(),
    )
    demo_page.route("**/api/subscriptions", gateway_timeout)
    timed_out = flow.wizard.submit_parties_tracked()
    demo_page.unroute("**/api/subscriptions")
    assert timed_out.sent and timed_out.outcome.status == 504
    assert not timed_out.may_resend, "a 504 may have acted and must never be resent"
    written = client.ok(
        "POST", "/api/search", {"resource": "subscriptions", "text": other["display_name"]}
    )
    assert written["total"] == 1, "the service did act on the call the browser saw fail"


def test_billing_contact_change_reads_back_on_the_read_only_overview(
    demo_page, demo_sut, demo_control, demo_seed
):
    demo_control.freeze(support.NOW)
    before = {"name": "Mae Before", "email": "mae.before@example.test"}
    sub, draft = deep_linked_change(demo_page, demo_sut, demo_seed, billing_contact=before)
    assert read_field(demo_page, label="Billing contact name").raw == "Mae Before"
    draft.set_billing_contact("Quill Afterward", "quill@example.test")
    result = draft.confirm("confirm_change")
    assert result.outcome.status == 200, result.outcome.describe()

    detail = SubscriptionDetail(demo_page, demo_sut.base_url)
    detail.wait_loaded()
    shown = read_field(demo_page, path="billing-contact")
    assert shown.raw == "Quill Afterward" and shown.via == "test-id"
    assert field_is_read_only(demo_page, path="billing-contact") is True
    contact = support.subscription(demo_sut.client, sub["id"])["billing_contact"]
    assert contact == {"name": "Quill Afterward", "email": "quill@example.test"}


def test_an_individual_plan_change_records_the_path_it_took(
    demo_page, demo_sut, demo_control, demo_seed, record_property
):
    """A deep-linked change on an individual subscription, with what it traversed recorded."""
    demo_control.freeze(support.NOW)
    sub, draft = deep_linked_change(demo_page, demo_sut, demo_seed, segment="individual")
    trail = {"steps": draft.steps(), "price_typed": False, "confirmed": False}
    draft.set_effective_date(support.date_of("2026-04-01"))
    draft.set_addon("sandbox", True)
    draft.set_monthly_net("12.40")
    trail["price_typed"] = True
    result = draft.confirm("confirm_change")
    trail["confirmed"] = bool(result.outcome.advanced)
    record_property("change_trail", json.dumps(trail))

    assert trail == {"steps": ["details", "price"], "price_typed": True, "confirmed": True}
    changed = support.subscription(demo_sut.client, sub["id"], version=2)
    assert changed["plan_kind"] == "INDIVIDUAL" and changed["addons"]["sandbox"]
    assert support.money(changed["price"]["monthly_net"]) == Decimal("12.40")
    (change,) = support.transactions(demo_sut.client, sub["id"], "CHANGE")
    assert change["period_start"] == "2026-04-01"
