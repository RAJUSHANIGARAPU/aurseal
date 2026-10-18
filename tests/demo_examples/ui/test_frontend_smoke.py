"""Front-end smoke over the demo screens: each check compares the screen with the API call
that fed it, so a screen that renders but shows the wrong data does not pass."""

from __future__ import annotations

import re
import uuid

import pytest

from tests.demo_examples.ui import support
from ui.pages.demo.base import DemoPage
from ui.pages.demo.lists import SubscriptionList, TaskList
from ui.pages.demo.subscription_detail import SubscriptionDetail
from ui.pages.demo.subscription_wizard import SubscriptionWizard

pytestmark = pytest.mark.demo_ui


def test_version_selector_loads_older_version(demo_page, demo_sut, demo_control, demo_seed):
    demo_control.freeze(support.NOW)
    sub = support.booked(demo_seed)
    draft = demo_seed.draft(sub["id"], "change", effective_date="2026-03-10")
    demo_seed.client.ok(
        "PATCH", f"/api/drafts/{draft['id']}", {"changes": {"contact_person": "Version Two"}}
    )
    demo_seed.confirm_draft(draft["id"])

    detail = SubscriptionDetail(demo_page, demo_sut.base_url).open(sub["id"])
    assert detail.current_version() == 2 and len(detail.version_options()) == 2
    capture = detail.select_version(1)

    assert re.search(rf"/ui/subscriptions/{sub['id']}\?version=1$", demo_page.url)
    assert capture.count >= 1, "the version GET was not captured (or did not answer 2xx)"
    loaded = capture.latest
    assert loaded["version"] == 1 and loaded["version_status"] != "current"
    assert detail.current_version() == 1 and not detail.version_is_current()
    assert detail.field("contact-person") != "Version Two"
    assert detail.actions() == [], "a superseded version must not offer lifecycle actions"


def test_reference_picker_matches_search_results(demo_page, demo_sut, demo_control, demo_seed):
    demo_control.freeze(support.NOW)
    support.segment_customer(demo_seed, "company")  # the list is never empty on a fresh worker
    wizard = SubscriptionWizard(demo_page, demo_sut.base_url).open()
    with demo_page.expect_response(lambda r: r.url.endswith("/api/search")) as caught:
        wizard.open_customer_picker()
    result = caught.value.json()
    assert caught.value.status == 200
    wizard.resolve("picker.row").first.wait_for()
    assert len(wizard.picker_rows()) == len(result["items"]) > 0

    first = result["items"][0]
    wizard.resolve("picker.row").first.click()
    wizard.resolve("picker.dialog").wait_for(state="hidden")
    assert wizard.summary("customer").startswith(first["number"])
    assert wizard.change_offered("customer")

    partner = demo_seed.partner()
    wizard.pick_partner(partner["number"])
    wizard.choose_plan("Team plan (TEAM)")
    customer = demo_seed.client.ok("GET", f"/api/customers/{first['id']}")
    if customer["status"] != "verified":
        wizard.run_screening()
    wizard.wait_for_address_block()
    usable = wizard.address_block().filled
    outcome = wizard.submit_parties()
    if usable:
        assert outcome.status == 201 and outcome.advanced, outcome.describe()
    else:  # the first customer in the list may have no usable billing address
        assert outcome.status == 422 and outcome.rejected


def test_task_list_matches_api(demo_page, demo_sut, demo_control, demo_seed):
    demo_control.freeze(support.NOW)
    sub = support.booked(demo_seed)
    demo_sut.client.ok(
        "POST", "/api/tasks", {"title": f"Check {sub['number']}", "subscription_id": sub["id"]}
    )
    tasks = demo_sut.client.ok("GET", "/api/tasks")
    screen = TaskList(demo_page, demo_sut.base_url).open()
    rows = screen.rows()
    assert len(rows) == len(tasks)
    first, api_first = rows[0], tasks[0]
    assert first["id"] == str(api_first["id"]) and first["title"] == api_first["title"]
    assert first["state"] == api_first["state"]
    assert first["subscription"] == (api_first["subscription_number"] or "")

    linked = next(t for t in tasks if t["subscription_id"] == sub["id"])
    screen.open_from_menu(linked["id"])
    assert demo_page.url.endswith(f"/ui/subscriptions/{sub['id']}?version={linked['version']}")
    detail = SubscriptionDetail(demo_page, demo_sut.base_url)
    detail.wait_loaded()
    assert detail.status() == "active"


def test_search_chip_filters_and_keyset_pages(demo_page, demo_sut, demo_control, demo_seed):
    demo_control.freeze(support.NOW)
    token = f"Keyset{uuid.uuid4().hex[:8]}"
    customer = support.segment_customer(demo_seed, "company", display_name=f"{token} Group")
    seeded = [
        support.segment_subscription(demo_seed, "company", customer=customer) for _ in range(5)
    ]

    listing = SubscriptionList(demo_page, demo_sut.base_url).open(limit=2)
    assert listing.paging_enabled()["prev"] is False
    call = listing.filter_by(token)
    assert call.status == 200 and call.body["text"] == token
    assert listing.chip_text() == token
    seen = []
    pages = [listing.rows()]
    while listing.paging_enabled()["next"]:
        call = listing.next_page()
        assert call.body.get("after"), "the next page did not send a keyset cursor"
        pages.append(listing.rows())
    for page in pages:
        assert page and all(token in row["title"] for row in page), page
        seen += [row["id"] for row in page]
    assert len(seen) == len(set(seen)) == 5, f"duplicates or gaps across pages: {seen}"
    assert sorted(map(int, seen)) == sorted(s["id"] for s in seeded)

    back = listing.previous_page()
    assert back.body.get("before") and [r["id"] for r in listing.rows()] == [
        r["id"] for r in pages[-2]
    ]


def test_post_deploy_menu_click_through_submit(demo_page, demo_sut, demo_control, demo_seed):
    demo_control.freeze(support.NOW)
    sub = support.booked(demo_seed)
    shell = DemoPage(demo_page, demo_sut.base_url)
    shell.goto("/ui")
    for key in ("subscriptions", "new-subscription", "customers", "tasks", "admin"):
        assert shell.resolve("menu.item", key=key).is_visible(), f"menu entry {key} missing"

    with demo_page.expect_navigation():
        shell.resolve("menu.item", key="admin").click()
    assert shell.title() == "Admin" and len(shell.resolve("admin.card_heading").all()) > 0

    with demo_page.expect_navigation():
        shell.resolve("menu.item", key="subscriptions").click()
    listing = SubscriptionList(demo_page, demo_sut.base_url)
    listing.resolve("list.row").first.wait_for()
    with demo_page.expect_response(re.compile(r"/api/subscriptions/\d+\?version=")) as caught:
        listing.open_row(0)
    assert caught.value.status == 200
    detail = SubscriptionDetail(demo_page, demo_sut.base_url)
    detail.wait_loaded()
    assert detail.resolve("detail.status_pill").first.is_visible()

    tasks = TaskList(demo_page, demo_sut.base_url).open()
    title = f"Smoke {uuid.uuid4().hex[:6]}"
    assert tasks.create(title, subscription_id=sub["id"]) == 201
    created = next(r for r in tasks.rows() if r["title"] == title)
    assert created["state"] == "OPEN" and created["subscription"] == sub["number"]
