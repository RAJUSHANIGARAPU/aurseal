"""The thin manual runner: spec parsing, step rules, and results in the same store."""

from __future__ import annotations

import pytest

from qaplatform import manual
from qaplatform.errors import BadRequest, Conflict
from tests.qaplatform._helpers import Api, finish, token_for

SPEC = """---
seed: true
feature: checkout
---

## Scenario Pay with a saved card

### Preconditions
- A customer with a saved card

### Steps
1. Open the basket
2. Choose the saved card
3. Confirm the payment

### Expected
- The order shows as paid

### Tags
`checkout` REQ-12

## Scenario Refund a cancelled order

### Steps
1. Cancel the order
2. Open the refunds page

### Expected
- The refund is listed

### Tags
REQ-13
"""


def test_parse_spec_reads_the_repository_spec_format():
    cases = manual.parse_spec(SPEC)
    assert [c.key for c in cases] == [
        "manual::pay-with-a-saved-card",
        "manual::refund-a-cancelled-order",
    ]
    first = cases[0]
    assert first.steps == ["Open the basket", "Choose the saved card", "Confirm the payment"]
    assert first.expected == ["The order shows as paid"]
    assert first.preconditions == ["A customer with a saved card"]
    assert first.requirements == ["REQ-12"]
    assert manual.parse_spec("## Scenario without steps\n### Expected\n- x\n") == []


def _steps(*statuses, evidence=False):
    return [
        {
            "step_no": i,
            "status": s,
            "actual": f"a{i}",
            "evidence_uri": f"shots/{i}.png" if evidence else None,
        }
        for i, s in enumerate(statuses, 1)
    ]


@pytest.mark.parametrize(
    "statuses, evidence, status, outcome, coverage",
    [
        (("pass", "pass"), True, "passed", "passed", "proved"),
        (("pass", "pass"), False, "passed", "passed", "recorded"),
        (("pass", "na"), False, "passed", "passed", "recorded"),
        (("pass", "fail", "blocked"), False, "failed", "failed", None),
        (("pass", "blocked"), False, "blocked", "skipped", None),
        (("na", "na"), False, "skipped", "skipped", None),
    ],
)
def test_step_rules(statuses, evidence, status, outcome, coverage):
    result = manual.outcome_of(_steps(*statuses, evidence=evidence))
    assert (result.status, result.outcome, result.coverage) == (status, outcome, coverage)


def test_blocked_is_never_executed_and_says_why():
    result = manual.outcome_of(_steps("pass", "blocked"))
    assert result.skip_reason == "blocked at step 2: a2"


def test_revision_pins_steps_and_expected_only():
    a = {"steps": ["x", "y"], "expected": ["z"], "title": "one"}
    assert manual.revision_id(a) == manual.revision_id({**a, "title": "two"})
    assert manual.revision_id(a) != manual.revision_id({**a, "steps": ["x", "y2"]})


@pytest.fixture
def tester(service, store):
    return Api(service, token_for(store, "tess", "tester"))


def _start(api, spec=SPEC, **extra):
    resp = api(
        "POST",
        "/projects/default/manual-runs",
        {"schema_version": "1.0", "name": "release check", "spec": spec, **extra},
    )
    assert resp.status == 201, resp.payload
    return resp.payload


def _record(api, ex_id, n, status, **extra):
    return api(
        "PUT",
        f"/projects/default/manual-executions/{ex_id}/steps/{n}",
        {"schema_version": "1.0", "status": status, **extra},
    )


def test_full_manual_run_lands_next_to_automated_results(tester, api):
    doc = _start(tester, release="2.4.0")
    run_id = doc["run"]["run_id"]
    assert doc["run"]["source"] == "manual" and doc["run"]["tally"]["selected"] == 2
    pay, refund = doc["executions"]
    for n in (1, 2, 3):
        _record(tester, pay["id"], n, "pass", evidence_uri=f"https://files.example.test/{n}.png")
    _record(tester, refund["id"], 1, "pass")
    _record(tester, refund["id"], 2, "fail", actual="refund missing")
    assert (
        tester("POST", f"/projects/default/manual-executions/{pay['id']}/finish").payload[
            "coverage"
        ]
        == "proved"
    )
    failed = tester("POST", f"/projects/default/manual-executions/{refund['id']}/finish").payload
    assert failed["outcome"] == "failed" and failed["message"] == "step 2 failed: refund missing"
    summary = finish(tester, run_id).payload
    assert summary["tally"]["passed"] == 1 and summary["tally"]["failed"] == 1
    assert summary["verdicts"]["coverage"]["proved"] == 1
    trace = {
        r["requirement"]: r["status"]
        for r in api("GET", "/projects/default/traceability").payload["rows"]
    }
    assert trace == {"REQ-12": "proved", "REQ-13": "failing"}
    evidence = api("GET", f"/projects/default/runs/{run_id}/evidence").payload["links"]
    assert {e["kind"] for e in evidence} == {"screenshot"} and len(evidence) == 3


def test_unfinished_execution_keeps_the_run_from_passing(tester):
    doc = _start(tester)
    pay = doc["executions"][0]
    for n in (1, 2, 3):
        _record(tester, pay["id"], n, "pass")
    tester("POST", f"/projects/default/manual-executions/{pay['id']}/finish")
    summary = finish(tester, doc["run"]["run_id"]).payload
    assert summary["integrity"]["verdict"] == "UNJUDGED"


def test_finishing_needs_every_step_and_happens_once(service, store, tester):
    doc = _start(tester)
    ex = doc["executions"][0]
    _record(tester, ex["id"], 1, "pass")
    resp = tester("POST", f"/projects/default/manual-executions/{ex['id']}/finish")
    assert resp.status == 400 and "2, 3" in resp.payload["detail"]
    _record(tester, ex["id"], 2, "pass")
    _record(tester, ex["id"], 3, "blocked", actual="test card rejected")
    done = tester("POST", f"/projects/default/manual-executions/{ex['id']}/finish").payload
    assert (done["status"], done["outcome"]) == ("blocked", "skipped")
    p = service.authenticate(tester.token)
    with pytest.raises(Conflict):
        service.finish_manual_execution(p, "default", ex["id"])
    with pytest.raises(Conflict):
        service.record_manual_step(
            p, "default", ex["id"], 1, {"schema_version": "1.0", "status": "pass"}
        )
    with pytest.raises(BadRequest):
        service.record_manual_step(
            p, "default", doc["executions"][1]["id"], 9, {"schema_version": "1.0", "status": "pass"}
        )


def test_execution_pins_the_revision_it_followed(tester, api):
    first = _start(tester)
    ex = first["executions"][0]
    edited = SPEC.replace("3. Confirm the payment", "3. Confirm the payment with a PIN")
    second = _start(tester, spec=edited)
    assert second["executions"][0]["revision_id"] != ex["revision_id"]
    again = api("GET", f"/projects/default/manual-runs/{first['run']['run_id']}").payload
    assert again["executions"][0]["revision_id"] == ex["revision_id"]
    assert again["executions"][0]["steps"][2] == "Confirm the payment"


def test_a_viewer_cannot_record(service, store, tester):
    doc = _start(tester)
    viewer = Api(service, token_for(store, "vic", "viewer"))
    assert _record(viewer, doc["executions"][0]["id"], 1, "pass").status == 403
    assert viewer("GET", f"/projects/default/manual-runs/{doc['run']['run_id']}").status == 200


def test_cases_can_be_sent_as_json(tester):
    ok = tester(
        "POST",
        "/projects/default/manual-runs",
        {
            "schema_version": "1.0",
            "name": "n",
            "cases": [
                {"key": "manual::a", "title": "A", "steps": ["one"], "requirements": ["REQ-1"]}
            ],
        },
    )
    assert ok.status == 201 and ok.payload["executions"][0]["requirements"] == ["REQ-1"]
    doc = tester(
        "POST",
        "/projects/default/manual-runs",
        {
            "schema_version": "1.0",
            "name": "n",
            "cases": [
                {"key": "manual::a", "steps": ["one"]},
                {"key": "manual::a", "steps": ["two"]},
            ],
        },
    )
    assert doc.status == 400 and "unique" in doc.payload["detail"]
    empty = tester(
        "POST",
        "/projects/default/manual-runs",
        {"schema_version": "1.0", "name": "n", "spec": "no scenarios here"},
    )
    assert empty.status == 400


def test_import_path_for_external_tools(tester):
    """An external tool pushes manual outcomes through the generic endpoints instead."""
    run = tester(
        "POST",
        "/projects/default/runs",
        {"schema_version": "1.0", "name": "tms export", "source": "manual"},
    ).payload
    resp = tester(
        "POST",
        f"/projects/default/runs/{run['run_id']}/results",
        {
            "schema_version": "1.0",
            "results": [
                {
                    "test_id": "tms::C101",
                    "outcome": "passed",
                    "steps": [{"no": 1, "status": "pass"}],
                }
            ],
        },
    )
    assert resp.payload["added"] == 1
