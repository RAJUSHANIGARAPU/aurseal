"""OpenAPI sync, the lifecycle machine, and the server-rendered screens' locator contract."""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser

import pytest
import yaml

from demo_sut import openapi
from demo_sut.app import CONFIG_DIR, router
from demo_sut.ui import ADMIN_CARDS, local_parts

# -- OpenAPI ----------------------------------------------------------------------


def test_committed_openapi_matches_the_route_table():
    committed = json.loads(openapi.SPEC_PATH.read_text())
    assert committed == openapi.build(), (
        "demo_sut/openapi.json is stale: run `python -m demo_sut openapi --write`"
    )


def test_every_api_route_is_documented_and_no_screen_is():
    spec = openapi.build()
    for route in router.routes:
        documented = route.method.lower() in spec["paths"].get(route.path, {})
        assert documented is route.api, (route.method, route.path)


def test_spec_operation_ids_are_unique():
    ids = [op["operationId"] for ops in openapi.build()["paths"].values() for op in ops.values()]
    assert len(ids) == len(set(ids))


# -- lifecycle machine ---------------------------------------------------------------

MACHINE = yaml.safe_load((CONFIG_DIR / "subscription.machine.yaml").read_text())


def reachable(machine):
    seen, todo = set(machine["initial_states"]), list(machine["initial_states"])
    while todo:
        for transition in machine["states"][todo.pop()]["transitions"]:
            if transition["to"] not in seen:
                seen.add(transition["to"])
                todo.append(transition["to"])
    return seen


def test_machine_is_self_consistent_with_one_unreachable_state():
    states = set(MACHINE["states"])
    for state in MACHINE["states"].values():
        for transition in state["transitions"]:
            assert transition["to"] in states
            assert transition["action"] in MACHINE["actions"]
    assert states - reachable(MACHINE) == {"suspended"}
    assert all(action["title"] for action in MACHINE["actions"].values())
    assert {f["name"] for f in MACHINE["fields"]} >= {"customer_id", "partner_id", "plan_id"}


def test_machine_is_served_by_the_api(demo_sut):
    assert demo_sut.client.ok("GET", "/api/machines/subscription") == MACHINE
    assert demo_sut.client.get("/api/machines/nope").status == 404


# -- screens ------------------------------------------------------------------------


class Collector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements = []

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


def parse(body: bytes):
    collector = Collector()
    collector.feed(body.decode())
    return collector.elements


def ids_of(elements):
    return {a.get("data-testid") for _, a in elements if a.get("data-testid")}


def actions(elements):
    return [a["data-action"] for _, a in elements if "data-action" in a]


@pytest.fixture
def sub(demo_seed):
    demo_seed.client.freeze("2026-03-10T09:00:00Z")
    return demo_seed.subscription(start_date="2026-03-01")


SCREENS = [
    "/ui",
    "/ui/subscriptions",
    "/ui/subscriptions/new",
    "/ui/customers",
    "/ui/customers/new",
    "/ui/tasks",
    "/ui/admin",
    "/ui/admin/accounts",
    "/ui/admin/integrations",
    "/ui/admin/settlements",
    "/ui/admin/plans",
    "/ui/admin/partners",
    "/ui/admin/jobs",
    "/ui/admin/users",
    "/ui/admin/exports",
    "/ui/admin/archived-lists",
    "/login",
]


@pytest.mark.parametrize("path", SCREENS)
def test_every_screen_has_menu_fault_and_error_banners(demo_sut, demo_control, path):
    response = demo_sut.client.get(path)
    assert response.status == 200 and response.source == "ui"
    ids = ids_of(parse(response.body))
    assert {"side-menu", "demo-faults-banner", "error-banner", "page-title"} <= ids


def test_fault_banner_lists_active_faults(demo_sut, demo_control):
    demo_control.faults("rounding_drift", "xml_escapes_twice")
    elements = parse(demo_sut.client.get("/ui").body)
    banner = next(a for _, a in elements if a.get("data-testid") == "demo-faults-banner")
    assert banner["data-faults"] == "rounding_drift,xml_escapes_twice"
    assert banner["data-active"] == "true"


def test_outage_fault_shows_the_error_banner(demo_sut, demo_control):
    demo_control.fault("api_unavailable")
    page = demo_sut.client.get("/ui/subscriptions")
    assert page.status == 503 and b"The service is unavailable" in page.body
    api = demo_sut.client.get("/api/plans")
    assert api.status == 503 and api.faults == ["api_unavailable"]
    assert demo_sut.client.get("/gw/subscriptions").status == 503
    assert demo_sut.client.get("/api/_admin/state").status == 200  # control stays reachable


def test_detail_buttons_follow_the_machine(demo_sut, sub):
    elements = parse(demo_sut.client.get(f"/ui/subscriptions/{sub['id']}").body)
    expected = [t["action"] for t in MACHINE["states"]["active"]["transitions"]]
    assert actions(elements) == expected
    ids = ids_of(elements)
    assert {
        "status-pill",
        "version-select",
        "ledger-grid",
        "generate-messages",
        "download-document",
    } <= ids
    rows = [a for _, a in elements if a.get("data-testid") == "ledger-row"]
    assert len(rows) == 12


def test_draft_screens_expose_their_confirm_actions(demo_sut, demo_seed, sub):
    for kind, action in (("change", "confirm_change"), ("cancel", "confirm_cancel")):
        draft = demo_seed.draft(sub["id"], kind)
        elements = parse(demo_sut.client.get(f"/ui/drafts/{draft['id']}").body)
        assert actions(elements) == [action, "discard_draft"]
        demo_sut.client.ok("POST", f"/api/drafts/{draft['id']}/actions/discard")
    ids = ids_of(parse(demo_sut.client.get(f"/ui/drafts/{draft['id']}").body))
    assert "closure-precondition" in ids


def test_wizard_and_quote_step(demo_sut, demo_seed, sub):
    wizard = ids_of(parse(demo_sut.client.get("/ui/subscriptions/new").body))
    assert {
        "pick-partner",
        "pick-customer",
        "picker-dialog",
        "address-block",
        "parties-submit",
    } <= wizard
    draft_sub = demo_seed.subscription(state="draft")
    quote = ids_of(
        parse(demo_sut.client.get(f"/ui/subscriptions/{draft_sub['id']}/step/quote").body)
    )
    assert {
        "price-net",
        "price-tax-rate",
        "price-commission",
        "price-calculate",
        "activate-immediately",
        "price-submit",
    } <= quote


def test_admin_card_hides_the_retired_entry_but_page_is_reachable(demo_sut, demo_control):
    links = ids_of(parse(demo_sut.client.get("/ui/admin").body))
    expected = {f"admin-link-{k}" for _, _, entries in ADMIN_CARDS for k, _ in entries}
    assert expected <= links and "admin-link-archived-lists" not in links
    page = demo_sut.client.get("/ui/admin/archived-lists")
    assert page.status == 200 and "breadcrumb" in ids_of(parse(page.body))


def test_accounts_screen_disables_edit_exactly_for_managed(demo_sut, demo_control):
    elements = parse(demo_sut.client.get("/ui/admin/accounts").body)
    rows = [a for _, a in elements if a.get("data-testid") == "account-row"]
    edits = [(t, a) for t, a in elements if a.get("data-testid") == "account-edit"]
    assert len(rows) == len(edits)
    for row, (_, edit) in zip(rows, edits, strict=True):
        assert ("disabled" in edit) is (row["data-managed"] == "true")


@pytest.mark.parametrize(
    ("instant", "local_date", "local_time"),
    [
        ("2026-03-28T23:30:00Z", "2026-03-29", "00:30"),  # winter time, crosses midnight
        ("2026-03-29T01:30:00Z", "2026-03-29", "03:30"),  # just after the spring switch
        ("2026-10-25T00:30:00Z", "2026-10-25", "02:30"),  # before the autumn switch
        ("2026-10-25T01:30:00Z", "2026-10-25", "02:30"),  # after it: same wall clock
        ("2026-12-31T23:15:00Z", "2027-01-01", "00:15"),  # year end
    ],
)
def test_task_times_render_in_the_configured_zone(instant, local_date, local_time):
    assert local_parts(instant, "Europe/Berlin") == (local_date, local_time)


def test_tasks_screen_rows(demo_sut, sub):
    demo_sut.client.ok(
        "POST",
        "/api/tasks",
        {"title": "Zone check", "subscription_id": sub["id"], "target_at": "2026-12-31T23:15:00Z"},
    )
    body = demo_sut.client.get("/ui/tasks").body.decode()
    row = re.search(r'<tr data-testid="task-row".*?Zone check.*?</tr>', body, re.S).group(0)
    assert 'data-testid="task-date">2027-01-01<' in row and ">00:15<" in row
    assert f'href="/ui/subscriptions/{sub["id"]}?version=1"' in row


def test_login_screen(demo_sut, demo_control):
    bad = demo_sut.client.post("/login", form={"username": "demo-admin", "password": "x"})
    assert bad.status == 401 and "login-error" in ids_of(parse(bad.body))
    good = demo_sut.client.request(
        "POST", "/login", form={"username": "demo-admin", "password": "demo-password"}
    )
    # urllib follows the redirect to the dashboard
    assert good.status == 200 and "dashboard-subscriptions" in ids_of(parse(good.body))


def test_unknown_screen_is_a_ui_404(demo_sut):
    response = demo_sut.client.get("/ui/nowhere/at/all")
    assert response.status == 404 and response.source == "ui"


@pytest.mark.parametrize("label", ["Run screening", "Start check"])
def test_screening_button_label_follows_the_setting(demo_sut, demo_control, label):
    demo_control.settings(screening_label=label)
    body = demo_sut.client.get("/ui/subscriptions/new").body.decode()
    assert f'data-testid="screening-run">{label}</button>' in body
