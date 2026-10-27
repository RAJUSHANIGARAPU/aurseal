"""Same inputs, same hash and verdict; CLI exit codes; stored verdicts over REST."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from results.api import ApiError, dispatch
from results.store import open_store
from tests.traceability._release import (
    CANDIDATE,
    NOTHING_SHIPS,
    NOW,
    PASSED_GAPS,
    decide,
    policy,
    proved_run,
    reg,
)
from tests.traceability._runs import RunSpec, T, load_store, red, write_artifacts
from traceability import api  # noqa: F401 - registers the routes
from traceability.cli import main as trace_main
from traceability.evidence import ArtifactResultSource
from traceability.release import decide_release, decision_json, load_policy
from traceability.store import save_registry

ROOT = Path(__file__).resolve().parents[2]
M = "tests/test_m.py::"


def test_same_inputs_same_hash_and_verdict(tmp_path):
    a = decide(tmp_path / "a", [proved_run()])
    b = decide(tmp_path / "b", [proved_run()])
    assert (a.inputs_hash, a.verdict) == (b.inputs_hash, b.verdict)
    run = proved_run()
    run.verdicts.append(red("r1", M + "a", "BUG-1", "cleared"))
    assert decide(tmp_path / "c", [run]).inputs_hash != a.inputs_hash
    decision_json(a)  # validates against the schema


def test_hash_ignores_the_order_inputs_arrive_in(tmp_path):
    one = proved_run()
    two = proved_run()
    two.tests.reverse()
    reports_a = write_artifacts(tmp_path / "a", [one])
    reports_b = write_artifacts(tmp_path / "b", [two])
    pol, registry = policy(), reg(("REQ-1", "must"))
    ha = decide_release(CANDIDATE, ArtifactResultSource(reports_a), pol, registry=registry, now=NOW)
    hb = decide_release(CANDIDATE, ArtifactResultSource(reports_b), pol, registry=registry, now=NOW)
    assert ha.inputs_hash == hb.inputs_hash


def test_the_repository_policy_loads_strict():
    pol = load_policy(ROOT / "release-policy.yaml")
    assert "PASSING" in pol.requirements["must"].no_verdict and pol.check_defects


@pytest.mark.parametrize(
    "runs,code",
    [
        ([proved_run()], 0),
        ([RunSpec("r1", [T(M + "a", "failed", reqs=("REQ-1",))])], 1),
        ([], 3),
    ],
)
def test_cli_exit_codes(tmp_path, runs, code):
    reports = write_artifacts(tmp_path / "reports", runs)
    # GO needs every input: a delta (what ships) and a passing gap ratchet.
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "gaps.json").write_text(json.dumps(PASSED_GAPS))
    delta = tmp_path / "delta.json"
    delta.write_text(json.dumps(NOTHING_SHIPS))
    reg_file = tmp_path / "reg.yaml"
    reg_file.write_text("requirements:\n  - {id: REQ-1, title: one, priority: must}\n")
    pol = tmp_path / "policy.yaml"
    pol.write_text((ROOT / "release-policy.yaml").read_text().split("defects:")[0])
    out = tmp_path / "out"
    rc = trace_main(
        [
            "release",
            "decide",
            "--release",
            "R1",
            "--candidate",
            "rc1",
            "--reports",
            str(reports),
            "--registry",
            str(reg_file),
            "--policy",
            str(pol),
            "--waivers",
            str(tmp_path / "none.yaml"),
            "--out",
            str(out),
            "--delta",
            str(delta),
            "--candidates-dir",
            str(tmp_path),
        ]
    )
    assert rc == code
    doc = json.loads((out / "R1-rc1.json").read_text())
    verdict = {0: "GO", 1: "NO_GO", 3: "NO_VERDICT"}[code]
    assert doc["verdict"] == verdict
    lines = (out / "R1-rc1.md").read_text().splitlines()
    assert lines[0] == f"RELEASE={verdict} ENFORCEMENT=FAIL CANDIDATE=rc1"
    assert lines[2].startswith("## Release R1 / rc1")


def test_rest_candidate_and_verdict(tmp_path, monkeypatch):
    monkeypatch.chdir(ROOT)  # the repository policy and waivers files
    with open_store(str(tmp_path / "r.db")) as store:
        load_store(store, [proved_run()])
        save_registry(store, reg(("REQ-1", "must")))
        with pytest.raises(ApiError):
            dispatch(store, "POST", "/releases/R1/candidates", body={"candidate_id": "rc1"})
        dispatch(
            store,
            "POST",
            "/releases/R1/candidates",
            body={"candidate_id": "rc1", "run_ids": ["r1"], "version": "1.0.0"},
        )
        with pytest.raises(ApiError):
            dispatch(store, "GET", "/releases/R1/candidates/rc1/verdict")
        doc = dispatch(store, "POST", "/releases/R1/candidates/rc1/verdict")
        # the repository policy checks defects and no tracker is configured here: strict
        assert doc["verdict"] == "NO_VERDICT"
        got = dispatch(store, "GET", "/releases/R1/candidates/rc1/verdict")
        assert got["inputs_hash"] == doc["inputs_hash"] and got["matrix_snapshot_id"]
        with pytest.raises(ApiError):
            dispatch(store, "POST", "/releases/R1/candidates/nope/verdict")
