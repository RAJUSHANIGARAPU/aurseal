"""The framework-free HTTP pipeline and the generated OpenAPI document."""

from __future__ import annotations

import pytest

from qaplatform import schemas
from qaplatform.http import routes
from qaplatform.http.openapi import build_openapi
from tests.qaplatform._helpers import new_run


def test_discovery_needs_no_token(api):
    assert api("GET", "/health", token=None).payload == {"status": "ok"}
    ready = api("GET", "/ready", token=None)
    assert ready.status == 200 and ready.payload["status"] == "ready"
    version = api("GET", "/version", token=None).payload
    assert version["api"] == "v1" and version["schema_version"] == "1.0"
    assert api("GET", "/schemas/run-create", token=None).payload["title"] == "Run create"
    assert api("GET", "/schemas/nope", token=None).status == 404


def test_every_error_is_a_problem_document(api):
    for resp in (
        api("GET", "/nowhere"),
        api("GET", "/me", token=None),
        api("POST", "/projects/default/runs", raw=b"{not json"),
        api("POST", "/projects/default/runs", {"schema_version": "1.0"}),
    ):
        assert resp.content_type == routes.PROBLEM_TYPE
        assert schemas.errors_for("problem", resp.payload) == []
        assert resp.payload["request_id"] == resp.headers["X-Request-Id"]


def test_schema_errors_carry_pointers(api):
    resp = api(
        "POST", "/projects/default/runs", {"schema_version": "1.0", "name": "n", "colour": "red"}
    )
    assert resp.status == 400
    assert resp.payload["errors"][0]["pointer"] == ""
    assert "colour" in resp.payload["errors"][0]["message"]


def test_request_id_is_echoed(api):
    resp = api("GET", "/me", headers={"X-Request-Id": "req_from_client"})
    assert resp.headers["X-Request-Id"] == "req_from_client"


def test_body_limit(api, monkeypatch):
    run_id = new_run(api)
    big = b"x" * (routes.JSON_LIMIT + 1)
    resp = api("POST", f"/projects/default/runs/{run_id}/results", raw=big)
    assert resp.status == 413


def test_idempotency_key_replays_and_guards(api):
    body = {"schema_version": "1.0", "target": "smoke"}
    key = {"Idempotency-Key": "k-1"}
    first = api("POST", "/projects/default/run-requests", body, headers=key)
    again = api("POST", "/projects/default/run-requests", body, headers=key)
    assert first.status == again.status == 202
    assert first.payload["id"] == again.payload["id"]
    run_id = new_run(api)
    clash = api(
        "POST",
        f"/projects/default/runs/{run_id}/evidence",
        {"schema_version": "1.0", "links": []},
        headers=key,
    )
    assert clash.status == 409


def test_unexpected_failure_is_a_500_problem_without_internals(api, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("secret internals")

    monkeypatch.setattr(api.service, "me", boom)
    resp = api("GET", "/me")
    assert resp.status == 500
    assert "secret internals" not in str(resp.payload)


def test_openapi_lists_exactly_the_route_table():
    doc = build_openapi()
    listed = {(m.upper(), p) for p, ops in doc["paths"].items() for m in ops}
    assert listed == {(r.method, r.full_path) for r in routes.ROUTES}
    assert set(doc["components"]["schemas"]) == set(schemas.names())
    for route in routes.ROUTES:
        for name in (route.request_schema, route.response_schema):
            assert name is None or name in schemas.names()


def test_openapi_refs_resolve_inside_the_document():
    doc = build_openapi()

    def refs(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "$ref":
                    yield v
                else:
                    yield from refs(v)
        elif isinstance(node, list):
            for v in node:
                yield from refs(v)

    for ref in refs(doc):
        assert ref.startswith("#/components/schemas/")
        assert ref.rsplit("/", 1)[1] in doc["components"]["schemas"]


def test_route_names_are_unique_and_paths_unambiguous():
    names = [r.name for r in routes.ROUTES]
    assert len(names) == len(set(names))
    for r in routes.ROUTES:
        concrete = r.path.replace("{", "x").replace("}", "")
        assert routes.find(r.method, concrete)[0] is r


@pytest.mark.parametrize("schema", schemas.names())
def test_every_schema_is_valid_2020_12(schema):
    from jsonschema import Draft202012Validator

    Draft202012Validator.check_schema(schemas.load(schema))
