"""REST handlers: pure functions over the store, mounted later by the platform API."""

from __future__ import annotations

import json

import pytest

from results.api import ROUTES, ApiError, dispatch
from tests.results._fixtures import populate


def test_create_push_finish_and_read(store):
    created = dispatch(store, "POST", "/runs", body={"run_id": "ext-1", "framework": "go"})
    assert created == {"run_id": "ext-1"}
    body = "\n".join(
        json.dumps(r)
        for r in [
            {
                "type": "result",
                "schema_version": 1,
                "run_id": "ext-1",
                "nodeid": "pkg.TestA",
                "outcome": "failed",
            },
            {
                "type": "result",
                "schema_version": 1,
                "run_id": "ext-1",
                "nodeid": "pkg.TestB",
                "outcome": "passed",
            },
        ]
    )
    report = dispatch(store, "POST", "/runs/ext-1/bundle", body=body)
    assert report["added"]["result"] == 2
    again = dispatch(store, "POST", "/runs/ext-1/bundle", body=body)
    assert again["added"]["result"] == 0
    finished = dispatch(store, "PATCH", "/runs/ext-1", body={"integrity_verdict": "FAIL"})
    assert finished["status"] == "finished" and finished["integrity_verdict"] == "FAIL"
    run = dispatch(store, "GET", "/runs/ext-1")
    assert run["tally"]["failed"] == 1 and run["tally"]["passed"] == 1
    failed = dispatch(store, "GET", "/runs/ext-1/results", params={"outcome": "failed"})
    assert [r["nodeid"] for r in failed["results"]] == ["pkg.TestA"]


def test_generated_run_id(store):
    assert dispatch(store, "POST", "/runs", body={})["run_id"].startswith("api-")


def test_bundle_push_for_another_run_is_rejected(store):
    dispatch(store, "POST", "/runs", body={"run_id": "a"})
    line = json.dumps(
        {"type": "result", "schema_version": 1, "run_id": "b", "nodeid": "t", "outcome": "passed"}
    )
    with pytest.raises(ApiError) as err:
        dispatch(store, "POST", "/runs/a/bundle", body=line)
    assert err.value.status == 400


def test_schema_violation_is_a_400_with_the_line(store):
    dispatch(store, "POST", "/runs", body={"run_id": "a"})
    with pytest.raises(ApiError) as err:
        dispatch(store, "POST", "/runs/a/bundle", body='{"type": "result"}')
    assert err.value.status == 400 and "line 1" in err.value.detail


def test_reads(store):
    populate(store)
    assert [r["run_id"] for r in dispatch(store, "GET", "/runs")["runs"]] == ["r1"]
    all_attempts = dispatch(store, "GET", "/runs/r1/results", params={"attempts": "all"})
    assert len(all_attempts["results"]) == 4
    bundle = dispatch(store, "GET", "/runs/r1/bundle")
    assert bundle["content_type"] == "application/x-ndjson" and bundle["body"].count("\n") > 5
    schema = dispatch(store, "GET", "/schemas/bundle-v1")
    assert schema["$id"].endswith("bundle-v1.schema.json")


def test_errors(store):
    with pytest.raises(ApiError) as err:
        dispatch(store, "GET", "/runs/missing")
    assert err.value.status == 404
    with pytest.raises(ApiError):
        dispatch(store, "GET", "/schemas/nothing")
    with pytest.raises(ApiError):
        dispatch(store, "DELETE", "/runs")
    dispatch(store, "POST", "/runs", body={"run_id": "a"})
    with pytest.raises(ApiError):
        dispatch(store, "PATCH", "/runs/a", body={"status": "exploded"})
    with pytest.raises(ApiError):
        dispatch(store, "GET", "/runs/a/results", params={"outcome": "meh"})
    assert err.value.to_problem()["status"] == 404


def test_routes_are_unique():
    keys = [(m, p) for m, p, _ in ROUTES]
    assert len(keys) == len(set(keys))
