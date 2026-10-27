"""Tests for the anonymous release smoke: the API document and the 401 challenge."""

from __future__ import annotations

import json
from pathlib import Path

from api.clients.http_engine import ApiResponse
from oracles import smoke
from oracles.data_health import GetOnlyApi

DOC = json.loads((Path(__file__).resolve().parents[2] / "demo_sut" / "openapi.json").read_text())
TITLE = DOC["info"]["title"]
SECURED = {**DOC, "components": {**DOC["components"], "securitySchemes": {"bearer": {}}}}


def _api(routes: dict[str, ApiResponse]) -> GetOnlyApi:
    return GetOnlyApi(lambda path, params=None: routes.get(path, ApiResponse(404)))


def _doc(document) -> ApiResponse:
    return ApiResponse(200, json.dumps(document).encode(), {"Content-Type": "application/json"})


CHECKS = [
    smoke.SmokeCheck(
        "api document",
        "/openapi.json",
        (smoke.api_document(TITLE, ["/api/subscriptions", "/api/health"], "bearer"),),
    ),
    smoke.SmokeCheck("protected read", "/api/subscriptions", (smoke.auth_challenge(),)),
]


def _run(routes):
    return {r.name: r for r in smoke.run_smoke(_api(routes), CHECKS)}


def test_a_started_service_behind_auth_passes_anonymously():
    results = _run(
        {
            "/openapi.json": _doc(SECURED),
            "/api/subscriptions": ApiResponse(
                401, b"", {"WWW-Authenticate": 'Bearer realm="demo"'}
            ),
        }
    )
    assert all(r.ok for r in results.values()), smoke.describe(results.values())


def test_a_load_balancer_page_fails_both_checks():
    page = ApiResponse(200, b"<html>maintenance</html>")
    results = _run({"/openapi.json": page, "/api/subscriptions": ApiResponse(503)})
    assert "not JSON" in results["api document"].problems[0]
    assert "expected HTTP 401" in results["protected read"].problems[0]


def test_wrong_title_missing_path_and_scheme_are_each_named():
    other = {**DOC, "info": {"title": "Some other service"}, "paths": {}}
    problems = _run({"/openapi.json": _doc(other)})["api document"].problems
    joined = " | ".join(problems)
    assert "title" in joined and "lacks GET" in joined and "'bearer'" in joined


def test_a_401_without_the_service_challenge_fails():
    results = _run(
        {
            "/openapi.json": _doc(SECURED),
            "/api/subscriptions": ApiResponse(401, b"denied", {"WWW-Authenticate": "Basic"}),
        }
    )
    assert "without a Bearer challenge" in results["protected read"].problems[0]


def test_non_object_and_non_200_documents():
    check = smoke.api_document(TITLE)
    assert check(ApiResponse(200, b"[]")) == ["API document is not a JSON object"]
    assert "expected HTTP 200" in check(ApiResponse(503))[0]
    swagger2 = {"info": {"title": TITLE}, "securityDefinitions": {"bearer": {}}}
    assert smoke.api_document(TITLE, security_scheme="bearer")(_doc(swagger2)) == []
