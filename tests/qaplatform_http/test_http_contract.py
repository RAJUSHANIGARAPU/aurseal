"""Every endpoint through the real web framework: statuses, problem documents, schemas."""

from __future__ import annotations

from qaplatform import schemas
from qaplatform.http import routes

P = "/api/v1/projects/default"


def _ok(resp, status, schema=None):
    assert resp.status_code == status, resp.text
    if schema:
        assert schemas.errors_for(schema, resp.json()) == []
    return resp.json()


def test_discovery(http):
    assert _ok(http.get("/api/v1/health"), 200) == {"status": "ok"}
    assert _ok(http.get("/api/v1/ready"), 200)["status"] == "ready"
    assert _ok(http.get("/api/v1/version"), 200)["api"] == "v1"
    doc = _ok(http.get("/api/v1/openapi.json"), 200)
    assert doc["openapi"].startswith("3.1")
    assert _ok(http.get("/api/v1/schemas/test-result"), 200)["title"] == "Test result"
    assert _ok(http.get("/api/v1/me"), 200)["role"] == "admin"


def test_mounted_routes_are_exactly_the_route_table(http):
    app = http.app
    mounted = {
        (m, r.path) for r in app.routes if r.path.startswith(routes.PREFIX) for m in r.methods
    }
    assert mounted == {(r.method, r.full_path) for r in routes.ROUTES}
    doc = http.get("/api/v1/openapi.json").json()
    assert {(m.upper(), p) for p, ops in doc["paths"].items() for m in ops} == mounted


def test_push_and_read_every_endpoint(http):
    run = _ok(
        http.post(
            f"{P}/runs",
            json={"schema_version": "1.0", "name": "n", "external_id": "x1", "expected_count": 2},
        ),
        201,
        "run-summary",
    )
    rid = run["run_id"]
    assert (
        http.post(
            f"{P}/runs", json={"schema_version": "1.0", "name": "n", "external_id": "x1"}
        ).status_code
        == 200
    )
    _ok(
        http.post(
            f"{P}/runs/{rid}/results",
            json={
                "schema_version": "1.0",
                "results": [{"test_id": "a::one", "outcome": "passed", "requirements": ["REQ-1"]}],
            },
        ),
        200,
    )
    xml = b'<testsuite name="s"><testcase classname="a" name="two"/></testsuite>'
    _ok(
        http.post(
            f"{P}/runs/{rid}/junit", content=xml, headers={"Content-Type": "application/xml"}
        ),
        200,
    )
    _ok(
        http.post(
            f"{P}/runs/{rid}/verdicts",
            content=(
                b'{"nodeid": "a::one", "kind": "coverage", "subject": "REQ-1",'
                b' "verdict": "proved"}\n'
            ),
            headers={"Content-Type": "application/x-ndjson"},
        ),
        200,
    )
    _ok(
        http.post(
            f"{P}/runs/{rid}/evidence",
            json={"schema_version": "1.0", "links": [{"kind": "report", "uri": "reports/allure/"}]},
        ),
        200,
    )
    summary = _ok(
        http.post(f"{P}/runs/{rid}/finish", json={"schema_version": "1.0"}), 200, "run-summary"
    )
    assert summary["integrity"]["verdict"] == "PASS"
    _ok(http.get(f"{P}/runs/{rid}"), 200, "run-summary")
    assert len(_ok(http.get(f"{P}/runs/{rid}/results"), 200)["results"]) == 2
    assert _ok(http.get(f"{P}/runs/{rid}/evidence"), 200)["links"]
    assert _ok(http.get(f"{P}/runs"), 200)["runs"][0]["run_id"] == rid
    assert _ok(http.get(f"{P}/verdicts", params={"run_id": rid}), 200)["verdicts"]
    assert _ok(http.get(f"{P}/history", params={"test_id": "a::one"}), 200)["history"]
    trace = _ok(http.get(f"{P}/traceability"), 200, "traceability")
    assert trace["rows"][0]["status"] == "proved"
    assert _ok(http.get(f"{P}/requirements/REQ-1"), 200)["status"] == "proved"
    assert http.get(f"{P}/releases/1.0/verdict").status_code == 501
    req = _ok(
        http.post(f"{P}/run-requests", json={"schema_version": "1.0", "target": "s"}),
        202,
        "run-request-status",
    )
    _ok(http.get(f"{P}/run-requests/{req['id']}"), 200, "run-request-status")


def test_errors_are_problem_json(http):
    for resp in (
        http.get("/api/v1/nowhere"),
        http.get("/elsewhere"),
        http.get(f"{P}/runs", headers={"Authorization": ""}),
        http.post(f"{P}/runs", content=b"{", headers={"Content-Type": "application/json"}),
        http.get("/api/v1/projects/ghost/runs"),
    ):
        assert resp.headers["content-type"].startswith("application/problem+json")
        assert schemas.errors_for("problem", resp.json()) == []
    assert http.get(f"{P}/runs", headers={"Authorization": ""}).status_code == 401
    assert http.get("/api/v1/projects/ghost/runs").status_code == 404
