"""Every criterion can be met, unmet or without evidence; no evidence is never met."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from results.store import open_store
from tests.traceability._runs import (
    RunSpec,
    T,
    coverage,
    load_store,
    red,
    registry,
    write_artifacts,
)
from traceability.cli import main as trace_main
from traceability.evidence import ArtifactResultSource
from traceability.plans import Selection, evaluate_plan, load_plan

M = "tests/test_m.py::"


def _plan(tmp_path: Path, criteria: str, requirements="[REQ-1, REQ-2]") -> Path:
    path = tmp_path / "p1.yaml"
    path.write_text(
        "id: p1\nscope:\n  include:\n    - requirements: " + requirements + "\n"
        "exit_criteria:\n" + "".join(f"  {line}\n" for line in criteria.splitlines())
    )
    return path


def _eval(tmp_path, runs, criteria, **kw):
    reports = write_artifacts(tmp_path / "reports", runs)
    plan = load_plan(_plan(tmp_path, criteria, kw.pop("requirements", "[REQ-1, REQ-2]")))
    ev = evaluate_plan(
        plan, ArtifactResultSource(reports), registry=registry("REQ-1", "REQ-2"), **kw
    )
    return {c.name: c.result for c in ev.criteria}, ev


GREEN = RunSpec(
    "r1",
    [T(M + "a", reqs=("REQ-1",)), T(M + "b", reqs=("REQ-2",)), T(M + "d")],
    verdicts=[
        coverage("r1", M + "a", "REQ-1 x"),
        coverage("r1", M + "b", "REQ-2 y"),
        red("r1", M + "d", "BUG-1"),
    ],
)
ALL = """run_integrity: PASS
min_pass_rate: 1.0
no_failures_in_requirements: all
requirements_proved: all
detectors_reached_verdict: true
no_draft_evidence: true"""


def test_everything_met(tmp_path):
    got, ev = _eval(tmp_path, [GREEN], ALL)
    assert set(got.values()) == {"met"} and ev.result == "met"


def test_everything_unmet(tmp_path):
    run = RunSpec(
        "r1",
        [
            T(M + "a", "failed", reqs=("REQ-1",)),
            T(M + "b", reqs=("REQ-2",), draft=True),
            T(M + "d", "skipped"),
        ],
        integrity="FAIL",
        verdicts=[red("r1", M + "d", "BUG-1", "held_empty")],
    )
    got, ev = _eval(tmp_path, [run], ALL)
    # a FAIL-integrity run makes requirement criteria unverified, i.e. no evidence
    assert got["run_integrity"] == "unmet" and got["detectors_reached_verdict"] == "unmet"
    assert got["min_pass_rate"] == "unmet"
    assert got["no_failures_in_requirements"] == "no_evidence"
    assert ev.result == "unmet"


def test_failures_and_drafts_in_a_sound_run(tmp_path):
    run = RunSpec(
        "r1", [T(M + "a", "failed", reqs=("REQ-1",)), T(M + "b", reqs=("REQ-2",), draft=True)]
    )
    got, _ = _eval(tmp_path, [run], ALL)
    assert got["no_failures_in_requirements"] == "unmet"
    assert got["requirements_proved"] == "unmet"
    assert got["no_draft_evidence"] == "unmet"


def test_no_evidence_is_never_met(tmp_path):
    got, ev = _eval(tmp_path, [], ALL + "\nmax_open_defects: {any: 0}")
    assert got == {
        "run_integrity": "no_evidence",
        "min_pass_rate": "no_evidence",
        "no_failures_in_requirements": "no_evidence",
        "requirements_proved": "no_evidence",
        "detectors_reached_verdict": "no_evidence",
        "no_draft_evidence": "met",  # nothing rests on drafts; the others carry the gap
        "max_open_defects": "no_evidence",
    }
    assert ev.result == "no_evidence"


def test_passing_but_unproved_is_no_evidence_for_proof(tmp_path):
    run = RunSpec("r1", [T(M + "a", reqs=("REQ-1",)), T(M + "b", reqs=("REQ-2",))])
    got, _ = _eval(tmp_path, [run], "requirements_proved: [REQ-1]")
    assert got["requirements_proved"] == "no_evidence"


def test_open_defects_from_a_provider(tmp_path):
    def defects():
        return [{"id": "GH-1", "severity": "high"}, {"id": "GH-2", "severity": "low"}]

    got, _ = _eval(tmp_path, [GREEN], "max_open_defects: {high: 0}", open_defects=defects)
    assert got["max_open_defects"] == "unmet"
    got, _ = _eval(tmp_path, [GREEN], "max_open_defects: {any: 2}", open_defects=defects)
    assert got["max_open_defects"] == "met"

    def down():
        raise ConnectionError("tracker down")

    got, _ = _eval(tmp_path, [GREEN], "max_open_defects: {any: 9}", open_defects=down)
    assert got["max_open_defects"] == "no_evidence"


def test_selection_limits_the_pass_rate(tmp_path):
    run = RunSpec("r1", [T(M + "a"), T(M + "outside", "failed")])
    got, _ = _eval(tmp_path, [run], "min_pass_rate: 1.0", selection=Selection([M + "a"]))
    assert got["min_pass_rate"] == "met"


def test_cli_evaluate_from_the_store(tmp_path):
    db = tmp_path / "r.db"
    with open_store(str(db)) as store:
        load_store(store, [GREEN])
    plan = _plan(tmp_path, "run_integrity: PASS\nrequirements_proved: all")
    reg = tmp_path / "reg.yaml"
    reg.write_text("requirements:\n  - {id: REQ-1, title: a}\n  - {id: REQ-2, title: b}\n")
    out = tmp_path / "out"
    rc = trace_main(
        [
            "plan",
            "evaluate",
            str(plan),
            "--db",
            str(db),
            "--runs",
            "r1",
            "--registry",
            str(reg),
            "--no-resolve",
            "--out",
            str(out),
            "--save",
        ]
    )
    assert rc == 0
    assert json.loads((out / "plan-p1.json").read_text())["result"] == "met"
    with open_store(str(db)) as store:
        from traceability.store import latest_plan_evaluation

        assert latest_plan_evaluation(store, "p1")["result"] == "met"


@pytest.mark.parametrize("result,code", [("unmet", 1), ("no_evidence", 3)])
def test_cli_exit_codes(tmp_path, result, code):
    reports = write_artifacts(
        tmp_path / "reports",
        []
        if result == "no_evidence"
        else [RunSpec("r1", [T(M + "a", reqs=("REQ-1",))], integrity="FAIL")],
    )
    plan = _plan(tmp_path, "run_integrity: PASS")
    rc = trace_main(
        [
            "plan",
            "evaluate",
            str(plan),
            "--reports",
            str(reports),
            "--no-resolve",
            "--out",
            str(tmp_path / "out"),
        ]
    )
    assert rc == code


def test_rest_plan_endpoints(tmp_path, monkeypatch):
    from results.api import ApiError, dispatch
    from traceability import api  # noqa: F401

    plans = tmp_path / "plans"
    plans.mkdir()
    _plan(plans, "run_integrity: PASS")
    monkeypatch.setenv("PLANS_DIR", str(plans))
    with open_store(str(tmp_path / "r.db")) as store:
        load_store(store, [GREEN])
        assert dispatch(store, "GET", "/plans")["plans"][0]["plan_id"] == "p1"
        assert dispatch(store, "GET", "/plans/p1")["body"]["id"] == "p1"
        with pytest.raises(ApiError):
            dispatch(store, "GET", "/plans/p1/evaluations/latest")
        ev = dispatch(store, "POST", "/plans/p1/evaluations", body={"run_ids": ["r1"]})
        assert ev["result"] == "met"
        assert dispatch(store, "GET", "/plans/p1/evaluations/latest")["result"] == "met"
        with pytest.raises(ApiError):
            dispatch(store, "GET", "/plans/nope")
