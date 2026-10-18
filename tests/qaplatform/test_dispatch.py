"""Run requests and the workflow_dispatch adapter."""

from __future__ import annotations

import pytest

from qaplatform.dispatch import (
    GitHubWorkflowDispatcher,
    NullDispatcher,
    from_env,
    parse_targets,
)
from tests.qaplatform._helpers import finish, new_run


class FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class FakeSession:
    def __init__(self, status: int = 204, boom: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self.status = status
        self.boom = boom

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        if self.boom:
            raise self.boom
        return FakeResponse(self.status)


def _github(session: FakeSession) -> GitHubWorkflowDispatcher:
    return GitHubWorkflowDispatcher(
        "example/suite",
        {"regression": "tests.yml"},
        "secret-token",
        ref="main",
        api_url="https://ci.example.test",
        session=session,
    )


REQUEST = {
    "id": "rq-1",
    "target": "regression",
    "environment": "staging",
    "release": None,
    "params": {"shard": 2, "headed": False},
}


def test_workflow_dispatch_payload():
    session = FakeSession()
    result = _github(session).dispatch(REQUEST)
    assert result.status == "dispatched"
    call = session.calls[0]
    assert call["url"] == (
        "https://ci.example.test/repos/example/suite/actions/workflows/tests.yml/dispatches"
    )
    assert call["json"] == {
        "ref": "main",
        "inputs": {
            "shard": "2",
            "headed": "False",
            "run_request_id": "rq-1",
            "environment": "staging",
        },
    }
    assert call["headers"]["Authorization"] == "Bearer secret-token"


@pytest.mark.parametrize(
    "request_, session, detail",
    [
        ({**REQUEST, "target": "nope"}, FakeSession(), "unknown target"),
        (REQUEST, FakeSession(status=422), "HTTP 422"),
        (REQUEST, FakeSession(boom=OSError("down")), "dispatch failed"),
    ],
)
def test_refusals_are_rejected_not_lost(request_, session, detail):
    result = _github(session).dispatch(request_)
    assert result.status == "rejected" and detail in result.detail
    assert "secret-token" not in result.detail


def test_configuration_from_env():
    assert isinstance(from_env({}), NullDispatcher)
    with pytest.raises(ValueError):
        from_env({"QAP_DISPATCHER": "github"})
    d = from_env(
        {
            "QAP_DISPATCHER": "github",
            "QAP_GITHUB_REPOSITORY": "o/r",
            "QAP_GITHUB_TOKEN": "t",
            "QAP_GITHUB_TARGETS": "smoke=smoke.yml, x",
        }
    )
    assert isinstance(d, GitHubWorkflowDispatcher) and d.targets == {"smoke": "smoke.yml"}
    assert parse_targets("a=1.yml,b=2.yml") == {"a": "1.yml", "b": "2.yml"}


def test_request_without_dispatcher_stays_queued_and_says_why(api):
    resp = api(
        "POST", "/projects/default/run-requests", {"schema_version": "1.0", "target": "regression"}
    )
    assert resp.status == 202
    assert resp.payload["status"] == "queued"
    assert "no dispatcher" in resp.payload["detail"]


def test_request_lifecycle_through_the_run_that_answers_it(service, api):
    service.dispatcher = _github(FakeSession())
    req = api(
        "POST",
        "/projects/default/run-requests",
        {"schema_version": "1.0", "target": "regression", "params": {"shard": 1}},
    ).payload
    assert req["status"] == "dispatched" and req["external_ref"] == "tests.yml"
    run_id = new_run(api, run_request_id=req["id"])
    polled = api("GET", f"/projects/default/run-requests/{req['id']}").payload
    assert (polled["status"], polled["run_id"]) == ("running", run_id)
    finish(api, run_id)
    assert api("GET", f"/projects/default/run-requests/{req['id']}").payload["status"] == (
        "finished"
    )


def test_run_naming_an_unknown_request_is_refused(api):
    resp = api(
        "POST",
        "/projects/default/runs",
        {"schema_version": "1.0", "name": "n", "run_request_id": "rq-missing"},
    )
    assert resp.status == 400
