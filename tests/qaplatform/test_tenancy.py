"""Multi-project isolation, enforced in the service layer and the scoped store.

Two projects are given *identical* external run ids, test ids, requirement keys and ticket
keys. Every read returns only its own project's rows, and a token of one project gets 404
(not 403: existence is not disclosed) for every route of the other.
"""

from __future__ import annotations

import inspect

import pytest

from qaplatform.errors import Conflict, Forbidden, NotFound
from qaplatform.http import routes
from qaplatform.store import PlatformStore, ScopedStore
from tests.qaplatform._helpers import Api, finish, new_run, push, token_for

SAME = [
    {
        "test_id": "checkout::pays",
        "outcome": "passed",
        "requirements": ["REQ-1"],
        "tickets": ["BUG-1"],
    },
    {"test_id": "checkout::refunds", "outcome": "failed", "requirements": ["REQ-1"]},
]


@pytest.fixture
def two(api, service, store):
    """Projects alpha and beta with the same ids everywhere; one tester token each."""
    for key in ("alpha", "beta"):
        assert api("POST", "/projects", {"schema_version": "1.0", "key": key}).status == 201
    out = {}
    for key, outcome in (("alpha", "passed"), ("beta", "failed")):
        client = Api(service, token_for(store, f"ci-{key}", "lead", key))
        run_id = new_run(client, key, external_id="ci-1", release="1.0")
        rows = [dict(SAME[0], outcome=outcome), SAME[1]]
        push(client, run_id, rows, key)
        client(
            "POST",
            f"/projects/{key}/runs/{run_id}/verdicts",
            [
                {
                    "test_id": "checkout::pays",
                    "kind": "coverage",
                    "subject": "REQ-1",
                    "verdict": "proved" if key == "alpha" else "recorded",
                }
            ],
        )
        finish(client, run_id, key)
        out[key] = (client, run_id)
    return out


def test_identical_ids_stay_apart(two):
    (alpha, a_run), (beta, b_run) = two["alpha"], two["beta"]
    assert a_run != b_run  # server-issued ids, the client's id is per project
    assert alpha("GET", "/projects/alpha/runs").payload["runs"][0]["external_id"] == "ci-1"
    a_hist = alpha("GET", "/projects/alpha/history", query={"test_id": "checkout::pays"})
    b_hist = beta("GET", "/projects/beta/history", query={"test_id": "checkout::pays"})
    assert [h["outcome"] for h in a_hist.payload["history"]] == ["passed"]
    assert [h["outcome"] for h in b_hist.payload["history"]] == ["failed"]
    a_trace = alpha("GET", "/projects/alpha/traceability").payload["rows"]
    b_trace = beta("GET", "/projects/beta/traceability").payload["rows"]
    assert (a_trace[0]["status"], b_trace[0]["status"]) == ("failing", "failing")
    assert [t["latest_verdict"] for t in a_trace[0]["tests"]] == ["proved", None]
    assert [t["latest_verdict"] for t in b_trace[0]["tests"]] == ["recorded", None]
    for client, key, run_id in ((alpha, "alpha", a_run), (beta, "beta", b_run)):
        verdicts = client("GET", f"/projects/{key}/verdicts").payload["verdicts"]
        assert {v["run_id"] for v in verdicts} == {run_id}
        audit = client("GET", f"/projects/{key}/audit").payload["entries"]
        assert {e["project"] for e in audit} == {key}


def _project_routes():
    for route in routes.ROUTES:
        if "{project}" in route.path:
            yield route


def test_a_token_of_one_project_gets_404_on_every_route_of_the_other(two):
    alpha, a_run = two["alpha"]
    _, b_run = two["beta"]
    checked = 0
    for route in _project_routes():
        path = (
            route.path.replace("{project}", "beta")
            .replace("{run_id}", b_run)
            .replace("{requirement}", "REQ-1")
            .replace("{release}", "1.0")
        )
        path = path.replace("{", "").replace("}", "")
        body = b"{}" if route.body else b""
        resp = alpha(route.method, path, raw=body, query={"test_id": "checkout::pays"})
        assert resp.status == 404, (route.method, route.path, resp.status)
        checked += 1
    assert checked >= 25


def test_a_run_is_not_reachable_through_another_projects_url(api, two):
    """Even an instance admin, who sees both projects, cannot read beta's run as alpha's."""
    _, b_run = two["beta"]
    assert api("GET", f"/projects/alpha/runs/{b_run}").status == 404
    assert api("GET", f"/projects/beta/runs/{b_run}").status == 200


def test_project_listing_never_reveals_other_projects(api, two):
    alpha, _ = two["alpha"]
    assert [p["key"] for p in alpha("GET", "/projects").payload["projects"]] == ["alpha"]
    keys = [p["key"] for p in api("GET", "/projects").payload["projects"]]
    assert keys == ["alpha", "beta", "default"]


def test_only_an_instance_admin_creates_projects(service, store, api):
    lead = service.authenticate(token_for(store, "dev", "admin"))
    with pytest.raises(Forbidden):
        service.create_project(lead, {"schema_version": "1.0", "key": "gamma"})
    created = api("POST", "/projects", {"schema_version": "1.0", "key": "gamma", "name": "G"})
    assert created.payload == {**created.payload, "key": "gamma", "role": "admin"}
    assert api("POST", "/projects", {"schema_version": "1.0", "key": "gamma"}).status == 409
    assert api("POST", "/projects", {"schema_version": "1.0", "key": "Bad Key"}).status == 400
    chain = store.scoped("gamma").audit_chain()
    assert [r["action"] for r in chain] == ["project.create"]


def test_archived_project_is_read_only_until_unarchived(api):
    run_id = new_run(api)
    resp = api("PATCH", "/projects/default", {"schema_version": "1.0", "archived": True})
    assert resp.payload["archived"] is True
    assert api("GET", f"/projects/default/runs/{run_id}").status == 200
    blocked = push(api, run_id, [{"test_id": "t", "outcome": "passed"}])
    assert blocked.status == 409 and "archived" in blocked.payload["detail"]
    api("PATCH", "/projects/default", {"schema_version": "1.0", "archived": False})
    assert push(api, run_id, [{"test_id": "t", "outcome": "passed"}]).status == 200


def test_unknown_project_is_not_found(service, admin):
    with pytest.raises(NotFound):
        service.get_project(admin, "ghost")
    with pytest.raises(Conflict):
        service.create_project(admin, {"schema_version": "1.0", "key": "default"})


def test_scoped_store_cannot_name_another_project():
    """No ScopedStore method takes a project argument: the scope is fixed at construction."""
    for name, fn in inspect.getmembers(ScopedStore, inspect.isfunction):
        if name.startswith("__"):
            continue
        params = set(inspect.signature(fn).parameters)
        assert not params & {"project", "project_id"}, name


def test_unscoped_store_surface_is_an_explicit_allowlist():
    """What PlatformStore offers without a scope: plumbing, tokens, users, the registry."""
    public = {n for n in vars(PlatformStore) if not n.startswith("_")}
    assert public == {
        "open",
        "close",
        "transaction",
        "scoped",
        "ready",
        "project",
        "projects",
        "projects_of",
        "create_project",
        "insert_token",
        "token_by_hash",
        "token",
        "tokens",
        "touch_token",
        "revoke_token",
        "user",
        "ensure_user",
        "binding",
    }


def test_cli_project_commands(db_path, capsys):
    import json

    from qaplatform.cli import main

    assert main(["project", "create", "--db", db_path, "shop", "--name", "Shop"]) == 0
    assert json.loads(capsys.readouterr().out)["key"] == "shop"
    assert main(["project", "list", "--db", db_path]) == 0
    keys = [p["key"] for p in json.loads(capsys.readouterr().out)["projects"]]
    assert "shop" in keys
