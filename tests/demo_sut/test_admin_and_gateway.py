"""Accounts, settlements, users, integrations, exports, tasks, audit, auth and the gateway."""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from demo_sut.admin import ACCOUNT_MESSAGES, base_url_problem


def test_accounts_carry_managed_flag_and_rename_rules(demo_sut, demo_control):
    client = demo_sut.client
    accounts = client.ok("GET", "/api/accounts")
    assert all(isinstance(a["managed"], bool) for a in accounts)
    managed = next(a for a in accounts if a["managed"])
    unmanaged = next(a for a in accounts if not a["managed"])
    refused = client.patch(f"/api/accounts/{managed['id']}", {"name": "Other"})
    assert refused.status == 409 and refused.json()["message"] == ACCOUNT_MESSAGES["managed"]
    renamed = client.ok("PATCH", f"/api/accounts/{unmanaged['id']}", {"name": "Renamed"})
    assert renamed["name"] == "Renamed"
    restored = client.ok("PATCH", f"/api/accounts/{unmanaged['id']}", {"name": unmanaged["name"]})
    assert restored["name"] == unmanaged["name"]


def test_settlement_statement_has_one_signed_entry(demo_sut, demo_seed):
    demo_seed.client.freeze("2026-03-10T09:00:00Z")
    terms = demo_sut.client.ok(
        "POST", "/api/reseller-terms", {"name": "Unit terms", "share_percent": 8, "flat_fee": 0.5}
    )
    partner = demo_seed.partner(reseller_terms_id=terms["id"])
    sub = demo_seed.subscription(partner_id=partner["id"], start_date="2026-03-01")
    demo_seed.book_due(sub["id"])
    net = sub["price"]["period_net"]
    expected = round(net * 8 / 100 + 0.5, 2)
    settlement = demo_sut.client.ok("POST", "/api/settlements", {"partner_id": partner["id"]})
    assert settlement["closing_balance"] == expected
    assert settlement["reference"].startswith("SET-")
    statement = demo_sut.client.ok("GET", f"/api/statements?settlement={settlement['id']}")
    assert statement["entries"] == [
        {
            "reference": settlement["reference"],
            "amount": expected,
            "side": "CREDIT",
            "party": partner["number"],
        }
    ]
    listed = demo_sut.client.ok("GET", f"/api/settlements?party={partner['id']}&tab=open")
    assert [s["id"] for s in listed] == [settlement["id"]]
    demo_sut.client.ok("POST", f"/api/settlements/{settlement['id']}/actions/close")
    assert demo_sut.client.ok("GET", f"/api/settlements?party={partner['id']}&tab=open") == []


@pytest.mark.parametrize("share", [0, 7.5, 20])
def test_reseller_terms_read_back_as_sent(demo_sut, demo_control, share):
    terms = demo_sut.client.ok(
        "POST", "/api/reseller-terms", {"name": "Read back", "share_percent": share, "flat_fee": 2}
    )
    stored = demo_sut.client.ok("GET", f"/api/reseller-terms/{terms['id']}")
    assert stored["share_percent"] == share and stored["flat_fee"] == 2.0


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"name": "No share"}, "share_percent"),
        ({"name": "Over", "share_percent": 21}, "share_percent"),
        ({"name": "Negative fee", "share_percent": 5, "flat_fee": -1}, "flat_fee"),
        ({"share_percent": 5}, "name"),
    ],
)
def test_reseller_terms_validation(demo_sut, demo_control, body, field):
    response = demo_sut.client.post("/api/reseller-terms", body)
    assert response.status == 422 and field in response.json()["fields"]


def test_a_new_subscription_takes_the_share_from_the_reseller_terms(demo_sut, demo_seed):
    terms = demo_sut.client.ok(
        "POST", "/api/reseller-terms", {"name": "Default share", "share_percent": 6.5}
    )
    partner = demo_seed.partner(reseller_terms_id=terms["id"])
    sub = demo_seed.subscription(partner_id=partner["id"], state="draft")
    assert sub["commission_percentage"] == 6.5


def test_role_history_names_the_roles(demo_sut, demo_control):
    client = demo_sut.client
    user = client.ok("GET", "/api/users")[1]
    client.ok("POST", f"/api/users/{user['id']}/roles", {"assign": ["AUDITOR"]})
    client.ok("POST", f"/api/users/{user['id']}/roles", {"revoke": ["AUDITOR"]})
    history = client.ok("GET", f"/api/users/{user['id']}/role-history")
    by_type = [(h["type"], h["roles"]) for h in history[-2:]]
    assert by_type == [("ASSIGNED", ["AUDITOR"]), ("REVOKED", ["AUDITOR"])]
    assert client.post(f"/api/users/{user['id']}/roles", {"assign": ["KING"]}).status == 422


@pytest.mark.parametrize(
    ("url", "problem"),
    [
        ("https://api.example.test/v1", None),
        ("https://hooks.example.test:8443/x", None),
        ("http://api.example.test", "Base URL must use https."),
        ("https://localhost:9000", "Base URL must not point at a private or loopback host."),
        ("https://127.0.0.1", "Base URL must not point at a private or loopback host."),
        ("https://10.1.2.3", "Base URL must not point at a private or loopback host."),
        ("https://192.168.0.5", "Base URL must not point at a private or loopback host."),
    ],
)
def test_base_url_rule(url, problem):
    assert base_url_problem(url) == problem


def test_integrations_list_optional_params_and_validate_base_url(demo_sut, demo_control):
    client = demo_sut.client
    services = client.ok("GET", "/api/integrations/services")
    assert all("baseUrl" in s["optionalParams"] for s in services)
    refused = client.post(
        "/api/integrations/mailer", {"apiKey": "k", "baseUrl": "https://127.0.0.1"}
    )
    assert refused.status == 400 and refused.json()["field"] == "baseUrl"
    ok = client.post(
        "/api/integrations/mailer", {"apiKey": "k", "baseUrl": "https://mail.example.test"}
    )
    assert ok.status == 201
    assert client.post("/api/integrations/mailer", {"apiKey": "k"}).status == 201
    assert client.post("/api/integrations/unknown", {"apiKey": "k"}).status == 404


def test_export_archive_and_attempt_fields(demo_sut, demo_control):
    client = demo_sut.client
    client.ok("POST", "/api/tasks", {"title": "Something to audit"})
    export = client.ok("POST", "/api/exports", {"kind": "audit"})
    assert export["attemptCount"] == 1 and export["lastAttemptAt"]
    assert client.ok("GET", f"/api/exports/{export['id']}") == export
    archive = client.get(f"/api/exports/{export['id']}/download")
    assert archive.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(archive.body)) as zf:
        rows = json.loads(zf.read("audit.json"))
    assert any(r["path"] == "/api/tasks" for r in rows)


def test_tasks_and_audit_trail(demo_sut, demo_seed):
    sub = demo_seed.subscription()
    task = demo_sut.client.ok(
        "POST",
        "/api/tasks",
        {"title": "Call", "subscription_id": sub["id"], "target_at": "2026-03-29T00:30:00+01:00"},
    )
    assert task["target_at"] == "2026-03-28T23:30:00Z" and task["state"] == "OPEN"
    assert task["version"] == 1 and task["subscription_number"] == sub["number"]
    done = demo_sut.client.ok("POST", f"/api/tasks/{task['id']}/actions/complete")
    assert done["state"] == "DONE"
    audit = demo_sut.client.ok("GET", "/api/audit?entity=admin")
    assert any(a["path"] == f"/api/tasks/{task['id']}/actions/complete" for a in audit)


@pytest.mark.parametrize("fault_on", [False, True])
def test_task_title_presence_only_fault(demo_sut, demo_control, fault_on):
    if fault_on:
        demo_control.fault("task_title_presence_only")
    for blank in ("", "   "):
        response = demo_sut.client.post("/api/tasks", {"title": blank})
        assert response.status == (201 if fault_on else 422), response.json()
    assert demo_sut.client.post("/api/tasks", {}).status == 422, "absence is always refused"


def test_run_tag_header_is_stored(demo_sut, demo_control):
    response = demo_sut.client.request(
        "POST", "/api/tasks", {"title": "Tagged"}, headers={"X-Test-Run-Id": "run-77"}
    )
    assert response.json()["run_tag"] == "run-77"


def test_token_grants(demo_sut, demo_control):
    client = demo_sut.client
    password = client.post(
        "/oauth/token",
        form={"grant_type": "password", "username": "demo-admin", "password": "demo-password"},
    )
    assert password.status == 200 and password.json()["token_type"] == "Bearer"
    token = password.json()["access_token"]
    me = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert me.json() == {"subject": "demo-admin"}
    machine = client.post(
        "/oauth/token",
        form={
            "grant_type": "client_credentials",
            "client_id": "demo-client",
            "client_secret": "demo-client-secret",
        },
    )
    assert machine.status == 200
    wrong = client.post(
        "/oauth/token",
        form={"grant_type": "password", "username": "demo-admin", "password": "nope"},
    )
    assert wrong.status == 401 and wrong.json()["error"] == "invalid_grant"
    odd = client.post("/oauth/token", form={"grant_type": "implicit"})
    assert odd.status == 400 and odd.json()["error"] == "unsupported_grant_type"


def test_auth_required_setting(demo_sut, demo_control):
    client = demo_sut.client
    demo_control.settings(auth_required=True)
    assert client.get("/api/plans").status == 401
    assert client.get("/api/health").status == 200
    token = client.post(
        "/oauth/token",
        form={
            "grant_type": "client_credentials",
            "client_id": "demo-client",
            "client_secret": "demo-client-secret",
        },
    )
    bearer = {"Authorization": f"Bearer {token.json()['access_token']}"}
    assert client.get("/api/plans", headers=bearer).status == 200


def test_gateway_no_route_differs_from_service_404(demo_sut, demo_control):
    client = demo_sut.client
    no_route = client.get("/gw/unknown-area/1")
    assert no_route.status == 404 and no_route.source == "gateway"
    assert no_route.json()["error"] == "no_route"
    missing = client.get("/gw/jobs/999999")
    assert missing.status == 404 and missing.source == "service"
    assert missing.json()["error"] == "not_found"
    assert missing.headers["x-demo-gateway"] == "forwarded"
    found = client.get("/gw/jobs?limit=1")
    assert found.status == 200 and found.headers["x-demo-gateway"] == "forwarded"
    unknown = client.get("/api/not-a-thing")
    assert unknown.status == 404 and unknown.json()["error"] == "unknown_path"
    assert client.request("DELETE", "/api/plans").status == 405


@pytest.mark.parametrize("fault_on", [False, True])
def test_share_scaled_twice_fault(demo_sut, demo_control, fault_on):
    if fault_on:
        demo_control.fault("reseller_share_scaled_twice")
    terms = demo_sut.client.ok(
        "POST", "/api/reseller-terms", {"name": "Scale", "share_percent": 6, "flat_fee": 1.25}
    )
    stored = demo_sut.client.ok("GET", f"/api/reseller-terms/{terms['id']}")
    assert stored["share_percent"] == (0.06 if fault_on else 6)
    assert stored["flat_fee"] == 1.25


@pytest.mark.parametrize("fault_on", [False, True])
def test_role_history_order_fault(tmp_path, fault_on):
    from demo_sut.client import DemoClient
    from demo_sut.server import DemoServer

    server = DemoServer(db_path=tmp_path / "roles.sqlite").start()  # ids start low here
    try:
        client = DemoClient(server.base_url)
        if fault_on:
            client.set_faults(role_history_sorted_as_text=True)
        user = client.ok("GET", "/api/users")[1]
        for _ in range(5):
            client.ok("POST", f"/api/users/{user['id']}/roles", {"assign": ["AUDITOR"]})
            client.ok("POST", f"/api/users/{user['id']}/roles", {"revoke": ["AUDITOR"]})
        ids = [e["id"] for e in client.ok("GET", f"/api/users/{user['id']}/role-history")]
        assert len(ids) >= 11
        assert (ids != sorted(ids)) is fault_on, ids
    finally:
        server.stop()
