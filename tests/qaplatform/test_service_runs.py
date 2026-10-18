"""Runs through the service: create, append, finish, and what the server refuses to claim."""

from __future__ import annotations

import pytest

from qaplatform.errors import BadRequest, NotFound, RunClosed
from tests.qaplatform._helpers import finish, new_run, push


def test_create_append_finish_round_trip(api):
    run_id = new_run(api, external_id="ci-1", expected_count=2, release="2.4.0")
    resp = push(
        api,
        run_id,
        [
            {"test_id": "billing.InvoiceTest#totals", "outcome": "passed", "duration_ms": 412},
            {"test_id": "billing.InvoiceTest#rounding", "outcome": "failed", "message": "9.99"},
        ],
    )
    assert resp.status == 200
    assert resp.payload == {"run_id": run_id, "received": 2, "added": 2, "duplicates": 0}
    summary = finish(api, run_id).payload
    assert summary["status"] == "finished"
    assert summary["release"] == "2.4.0"
    assert summary["tally"]["passed"] == 1 and summary["tally"]["failed"] == 1
    assert summary["integrity"]["verdict"] == "PASS"


def test_external_id_makes_create_idempotent(api):
    first = api(
        "POST",
        "/projects/default/runs",
        {"schema_version": "1.0", "name": "a", "external_id": "ci-7"},
    )
    again = api(
        "POST",
        "/projects/default/runs",
        {"schema_version": "1.0", "name": "a", "external_id": "ci-7"},
    )
    assert (first.status, again.status) == (201, 200)
    assert first.payload["run_id"] == again.payload["run_id"]


def test_resending_a_batch_adds_nothing(api):
    run_id = new_run(api)
    batch = [{"test_id": "t::a", "outcome": "passed"}, {"test_id": "t::b", "outcome": "passed"}]
    push(api, run_id, batch)
    again = push(api, run_id, batch).payload
    assert again["added"] == 0 and again["duplicates"] == 2


def test_append_after_finish_is_refused(service, admin, api):
    run_id = new_run(api)
    finish(api, run_id)
    with pytest.raises(RunClosed):
        service.append_results(
            admin,
            "default",
            run_id,
            {"schema_version": "1.0", "results": [{"test_id": "x", "outcome": "passed"}]},
        )
    resp = push(api, run_id, [{"test_id": "x", "outcome": "passed"}])
    assert resp.status == 409
    assert resp.payload["type"].endswith("/run-closed")


def test_shortfall_against_expected_count_is_not_assessed(api):
    run_id = new_run(api, expected_count=40)
    push(api, run_id, [{"test_id": f"t::{i}", "outcome": "passed"} for i in range(8)])
    integrity = finish(api, run_id).payload["integrity"]
    assert integrity["verdict"] == "UNJUDGED"
    assert integrity["assessed"] is False
    assert {"rule": "SELECTION", "detail": "8 of 40 declared test(s) reported a result"} in (
        integrity["breaches"]
    )


def test_expected_count_can_be_declared_at_finish(api):
    run_id = new_run(api)
    push(api, run_id, [{"test_id": "t::1", "outcome": "passed"}])
    summary = finish(api, run_id, expected_count=3).payload
    assert summary["tally"]["selected"] == 3
    assert summary["integrity"]["verdict"] == "UNJUDGED"


def test_all_skipped_run_is_never_pass(api):
    run_id = new_run(api, expected_count=3)
    push(api, run_id, [{"test_id": f"t::{i}", "outcome": "skipped"} for i in range(3)])
    integrity = finish(api, run_id).payload["integrity"]
    assert integrity["verdict"] == "FAIL"
    assert any(b["rule"] == "SKIP RATIO" for b in integrity["breaches"])


def test_missing_expected_count_is_named_not_assumed(api):
    run_id = new_run(api)
    push(api, run_id, [{"test_id": "t::1", "outcome": "passed"}])
    integrity = finish(api, run_id).payload["integrity"]
    assert any(r.startswith("selection - no expected_count") for r in integrity["rules_absent"])


def test_retried_flake_is_counted(api):
    run_id = new_run(api)
    push(
        api,
        run_id,
        [
            {"test_id": "t::flaky", "outcome": "failed", "attempt": 1},
            {"test_id": "t::flaky", "outcome": "passed", "attempt": 2},
        ],
    )
    summary = finish(api, run_id).payload
    assert summary["tally"]["passed"] == 1 and summary["tally"]["failed"] == 0
    assert summary["tally"]["flaky"] == 1
    every = api("GET", f"/projects/default/runs/{run_id}/results", query={"attempts": "all"})
    assert [r["outcome"] for r in every.payload["results"]] == ["failed", "passed"]


def test_finishing_twice_returns_the_same_verdict(api):
    run_id = new_run(api)
    push(api, run_id, [{"test_id": "t::1", "outcome": "passed"}])
    first = finish(api, run_id).payload
    second = finish(api, run_id, interrupted=True).payload
    assert second["status"] == "finished"
    assert second["integrity"]["verdict"] == first["integrity"]["verdict"]


def test_interrupted_run(api):
    run_id = new_run(api)
    assert finish(api, run_id, interrupted=True).payload["status"] == "interrupted"


def test_schema_violation_names_the_pointer(service, admin):
    run = service.create_run(admin, "default", {"schema_version": "1.0", "name": "n"})[1]
    with pytest.raises(BadRequest) as info:
        service.append_results(
            admin,
            "default",
            run["run_id"],
            {"schema_version": "1.0", "results": [{"test_id": "a", "outcome": "green"}]},
        )
    assert info.value.errors[0]["pointer"] == "/results/0/outcome"


def test_unknown_run_and_project_are_not_found(service, admin):
    with pytest.raises(NotFound):
        service.get_run(admin, "default", "r-nope")
    with pytest.raises(NotFound):
        service.get_run(admin, "nope", "r-nope")


def test_verdicts_json_is_strict_and_jsonl_is_tolerant(api):
    run_id = new_run(api)
    bad = api(
        "POST",
        f"/projects/default/runs/{run_id}/verdicts",
        [{"test_id": "t", "kind": "coverage", "verdict": "green"}],
    )
    assert bad.status == 400
    lines = "\n".join(
        [
            '{"nodeid": "t::a", "kind": "coverage", "subject": "REQ-1", "verdict": "proved"}',
            "not json",
            '{"nodeid": "t::b", "kind": "expected_red", "ticket": "BUG-142", "verdict": "nope"}',
            "",
            '{"nodeid": "t::c", "kind": "expected_red", "ticket": "BUG-142",'
            ' "verdict": "reproduces", "run_id": "local-1", "timestamp": "2026-01-05T10:12:00Z"}',
        ]
    )
    ok = api(
        "POST",
        f"/projects/default/runs/{run_id}/verdicts",
        raw=lines.encode(),
        content_type="application/x-ndjson",
    )
    assert ok.payload == {"run_id": run_id, "received": 2, "added": 2, "dropped": 2}
    rows = api("GET", "/projects/default/verdicts", query={"ticket": "BUG-142"}).payload
    assert [r["verdict"] for r in rows["verdicts"]] == ["reproduces"]


def test_verdicts_feed_the_summary_and_the_detector_rule(api):
    run_id = new_run(api)
    push(api, run_id, [{"test_id": "t::det", "outcome": "xfailed", "tags": ["known_defect"]}])
    api(
        "POST",
        f"/projects/default/runs/{run_id}/verdicts",
        [{"test_id": "t::det", "kind": "expected_red", "ticket": "BUG-9", "verdict": "reproduces"}],
    )
    summary = finish(api, run_id).payload
    assert summary["verdicts"]["expected_red"]["reproduces"] == 1
    assert summary["integrity"]["verdict"] == "PASS"


def test_evidence_links_after_finish(api):
    run_id = new_run(api)
    push(
        api,
        run_id,
        [
            {
                "test_id": "t::1",
                "outcome": "failed",
                "evidence": [{"kind": "trace", "uri": "reports/t1.zip"}],
            }
        ],
    )
    finish(api, run_id)
    resp = api(
        "POST",
        f"/projects/default/runs/{run_id}/evidence",
        {"schema_version": "1.0", "links": [{"kind": "report", "uri": "reports/allure/"}]},
    )
    assert resp.payload["added"] == 1
    links = api("GET", f"/projects/default/runs/{run_id}/evidence").payload["links"]
    assert {(link["kind"], link["uri"]) for link in links} == {
        ("trace", "reports/t1.zip"),
        ("report", "reports/allure/"),
    }


def test_manual_style_steps_land_in_the_trace(api):
    run_id = new_run(api, source="manual")
    push(
        api,
        run_id,
        [
            {
                "test_id": "manual::login",
                "outcome": "failed",
                "steps": [
                    {"no": 1, "status": "pass", "title": "open the page"},
                    {
                        "no": 2,
                        "status": "fail",
                        "title": "submit",
                        "actual": "error 500",
                        "evidence_uri": "reports/manual/step2.png",
                    },
                ],
            }
        ],
    )
    summary = api("GET", f"/projects/default/runs/{run_id}").payload
    assert summary["source"] == "manual" and summary["status"] == "open"
    links = api("GET", f"/projects/default/runs/{run_id}/evidence").payload["links"]
    assert links[0]["uri"] == "reports/manual/step2.png"


def test_case_history_and_traceability(api):
    for outcome in ("failed", "passed"):
        run_id = new_run(api)
        push(
            api,
            run_id,
            [
                {
                    "test_id": "billing::total",
                    "outcome": outcome,
                    "requirements": ["REQ-12"],
                    "tickets": ["BUG-142"],
                }
            ],
        )
        finish(api, run_id)
    hist = api("GET", "/projects/default/history", query={"test_id": "billing::total"}).payload
    assert [h["outcome"] for h in hist["history"]] == ["failed", "passed"]
    trace = api("GET", "/projects/default/traceability").payload
    assert trace["rows"][0]["requirement"] == "REQ-12"
    assert trace["rows"][0]["status"] == "recorded_only"  # passed, but nothing proved it
    assert trace["rows"][0]["open_tickets"] == ["BUG-142"]
    one = api("GET", "/projects/default/requirements/REQ-99").payload
    assert one["status"] == "no_tests"


def test_release_verdict_is_not_configured_by_default(api):
    resp = api("GET", "/projects/default/releases/2.4.0/verdict")
    assert resp.status == 501
    assert resp.payload["type"].endswith("/not-configured")


def test_release_verdict_reader_is_pluggable(service, admin):
    class Reader:
        def release_verdict(self, scoped, release):
            return {
                "project": scoped.project_id,
                "release": release,
                "decision": "no_go",
                "reasons": [{"rule": "proved", "result": "breached"}],
            }

    service.release_verdict_reader = Reader()
    doc = service.release_verdict(admin, "default", "2.4.0")
    assert doc["decision"] == "no_go" and doc["project"] == "default"


def test_list_runs_paginates_with_a_cursor(api):
    ids = [new_run(api, name=f"run-{i}") for i in range(5)]
    page = api("GET", "/projects/default/runs", query={"limit": "2"}).payload
    seen = [r["run_id"] for r in page["runs"]]
    while "next_cursor" in page:
        page = api(
            "GET", "/projects/default/runs", query={"limit": "2", "cursor": page["next_cursor"]}
        ).payload
        seen += [r["run_id"] for r in page["runs"]]
    assert sorted(seen) == sorted(ids)
    assert api("GET", "/projects/default/runs", query={"limit": "5000"}).status == 400
    assert api("GET", "/projects/default/runs", query={"cursor": "%%%"}).status == 400
