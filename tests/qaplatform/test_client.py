"""PlatformClient against the in-process pipeline (no server, no socket)."""

from __future__ import annotations

import json

import pytest

from qaplatform.client import PlatformClient
from qaplatform.errors import BadRequest, RunClosed, Unauthorized
from tests.qaplatform._helpers import InProcess


@pytest.fixture
def client(service, admin_token):
    return PlatformClient(
        "http://platform.example.test",
        admin_token,
        "default",
        session=InProcess(service),
        backoff_s=0,
    )


def test_full_push_cycle(client, tmp_path):
    run = client.create_run("nightly", external_id="ci-9", expected_count=1001)
    results = [{"test_id": f"t::{i}", "outcome": "passed"} for i in range(1001)]
    assert client.push_results(run["run_id"], results)["added"] == 1001
    assert client.session.calls == 3  # create + two batches
    vfile = tmp_path / "verdicts.jsonl"
    vfile.write_text(
        json.dumps({"nodeid": "t::1", "kind": "coverage", "subject": "REQ-1", "verdict": "proved"})
        + "\n",
        encoding="utf-8",
    )
    assert client.push_verdicts(run["run_id"], vfile)["added"] == 1
    summary = client.finish_run(run["run_id"])
    assert summary["integrity"]["verdict"] == "PASS"
    assert [v["subject"] for v in client.verdicts(run_id=run["run_id"])] == ["REQ-1"]
    assert client.traceability(requirement="REQ-1")["rows"][0]["status"] == "proved"
    with pytest.raises(RunClosed):
        client.push_results(run["run_id"], results[:1])


def test_verdict_iterator_follows_cursors(client):
    run = client.create_run("n")
    rows = [
        {"test_id": f"t::{i}", "kind": "coverage", "subject": "REQ-1", "verdict": "recorded"}
        for i in range(5)
    ]
    client.push_verdicts(run["run_id"], rows)
    assert len(list(client.verdicts(run_id=run["run_id"], limit=2))) == 5


def test_retries_transient_statuses(service, admin_token):
    session = InProcess(service, fail_first=[503, 429])
    c = PlatformClient("http://p.example.test", admin_token, session=session, backoff_s=0)
    assert c.me()["role"] == "admin"
    assert session.calls == 3


def test_errors_map_to_exceptions(service, client):
    bad = PlatformClient(
        "http://p.example.test", "qap_wrong", session=InProcess(service), backoff_s=0
    )
    with pytest.raises(Unauthorized):
        bad.me()
    with pytest.raises(BadRequest) as info:
        client.create_run("n", source="robot")
    assert info.value.errors


def test_health_never_raises(service, admin_token):
    c = PlatformClient(
        "http://p.example.test",
        admin_token,
        session=InProcess(service, [503] * 9),
        retries=1,
        backoff_s=0,
    )
    assert c.health()["status"] == "unavailable"


def test_run_request_round_trip(client):
    req = client.request_run("smoke", environment="staging")
    assert client.wait_for_run_request(req["id"], timeout_s=0)["status"] == "queued"
