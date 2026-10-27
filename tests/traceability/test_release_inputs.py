"""Every release input is required for GO; change scope, coverage gaps, build, snapshot age."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.traceability._release import FRESH, NOW, M, policy, proved_run, reg
from tests.traceability._runs import T, red, write_artifacts
from traceability.cli import main as trace_main
from traceability.defects import defects_provider
from traceability.evidence import ArtifactResultSource
from traceability.release import (
    BLOCK,
    NO_VERDICT,
    RELEASE_INPUTS,
    Candidate,
    ReleaseVerdict,
    decide_release,
)

DELTA = Path(__file__).parent / "fixtures" / "promotion-delta.json"
PLAN = (
    "id: p1\nscope:\n  include:\n    - requirements: [REQ-1]\n"
    "exit_criteria:\n  min_pass_rate: 1.0\n"
)
FULL = {
    "defects": {"blocking_severities": ["high"], "max_snapshot_age_hours": 48},
    "plan": "required",
}


def _world(tmp_path: Path, runs=None) -> Path:
    reports = write_artifacts(tmp_path / "reports", runs or [proved_run()])
    (reports / "gaps.json").write_text(
        json.dumps({"ratchet": {"new": [], "closed": [], "passed": True}})
    )
    (reports / "build-info.json").write_text(
        json.dumps({"component": "app", "version": "1.4.0", "measured": True, "run_id": "r1"})
    )
    (tmp_path / "p1.yaml").write_text(PLAN)
    return reports


def _decide(tmp_path, reports, *, delta="file", plan=True, defects=None, version="v1.4.0", **kw):
    from traceability.plans import load_plan

    return decide_release(
        Candidate("R1", "rc1", version),
        ArtifactResultSource(reports),
        kw.pop("pol", None) or policy(**FULL),
        registry=reg(("REQ-1", "must")),
        now=NOW,
        plan=load_plan(tmp_path / "p1.yaml") if plan else None,
        open_defects=defects or defects_provider([], FRESH),
        delta=json.loads(DELTA.read_text()) if delta == "file" else delta,
        **kw,
    )


def _remove(name: str, tmp_path: Path, reports: Path) -> dict:
    """Delete one input; return the decide() overrides that go with it."""
    if name == "integrity":
        for p in reports.rglob("run-trust*.json"):
            p.unlink()
    elif name == "verdicts":
        for p in reports.rglob("verdicts*.jsonl"):
            p.unlink()
    elif name == "case_links":
        for p in reports.rglob("case-links*.json"):
            p.unlink()
    elif name == "defect_snapshot":

        def gone():
            raise FileNotFoundError("no defect snapshot at reports/defect-status.json")

        return {"defects": gone}
    elif name == "plan":
        return {"plan": False}
    elif name == "change_scope":
        return {"delta": None}
    elif name == "coverage_gaps":
        (reports / "gaps.json").unlink()
    elif name == "build_info":
        (reports / "build-info.json").unlink()
    return {}


NAMES_THE_INPUT = {
    "integrity": "run-trust",
    "verdicts": "no proved coverage verdict",
    "case_links": "no case link",
    "defect_snapshot": "snapshot",
    "plan": "plan",
    "change_scope": "change scope unknown",
    "coverage_gaps": "coverage gaps not measured",
    "build_info": "version not measured",
}


def test_the_fixture_with_every_input_is_go(tmp_path):
    d = _decide(tmp_path, _world(tmp_path))
    assert d.verdict is ReleaseVerdict.GO, [r.to_json() for r in d.reasons]


def test_every_input_the_decision_records_is_in_the_removal_list(tmp_path):
    # A new input added to decide_release must be added to the property test below too.
    d = _decide(tmp_path, _world(tmp_path))
    assert list(d.inputs) == list(RELEASE_INPUTS)
    assert set(NAMES_THE_INPUT) == set(d.inputs)


@pytest.mark.parametrize("name", RELEASE_INPUTS)
def test_removing_any_one_input_is_never_go(tmp_path, name):
    reports = _world(tmp_path)
    d = _decide(tmp_path, reports, **_remove(name, tmp_path, reports))
    assert d.verdict is not ReleaseVerdict.GO
    text = " ".join(f"{r.criterion} {r.exhibit} {r.message}" for r in d.reasons)
    assert NAMES_THE_INPUT[name] in text


# ---------------------------------------------------------------------- E3 build


def _severity(d, needle):
    return [r.severity for r in d.reasons if needle in r.message]


def test_a_matching_measured_version_adds_no_reason(tmp_path):
    d = _decide(tmp_path, _world(tmp_path), version="1.4.0")  # "v" is normalised away
    assert not [r for r in d.reasons if r.kind == "build"]


def test_a_different_build_is_no_verdict(tmp_path):
    d = _decide(tmp_path, _world(tmp_path), version="1.5.0")
    assert d.verdict is ReleaseVerdict.NO_VERDICT
    assert any(
        "evidence for a different build" in r.message
        and "1.5.0" in r.message
        and "1.4.0" in r.message
        for r in d.reasons
    )


def test_a_pinned_component_without_a_probe_cannot_be_measured(tmp_path):
    d = _decide(tmp_path, _world(tmp_path), pins={"ledger": "2.0.0"})
    assert _severity(d, "ledger cannot be measured") == [NO_VERDICT]


# ---------------------------------------------------------------------- E4 change scope


def _flat(keys=(), regressing=()):
    return {"keys": list(keys), "regressing": list(regressing), "not_compared": []}


def test_a_regressing_commit_blocks(tmp_path):
    delta = _flat(regressing=[{"service": "billing", "commits": ["deadbeef0001"]}])
    d = _decide(tmp_path, _world(tmp_path), delta=delta)
    assert d.verdict is ReleaseVerdict.NO_GO and _severity(d, "would regress billing") == [BLOCK]


def test_a_shipped_key_whose_detector_still_reproduces_blocks(tmp_path):
    run = proved_run()
    run.tests.append(T(M + "d", "xfailed"))
    run.verdicts.append(red("r1", M + "d", "BUG-9"))
    delta = _flat(keys=[{"key": "BUG-9", "services": ["billing"], "mentioned_by": [M + "d"]}])
    d = _decide(tmp_path, _world(tmp_path, [run]), delta=delta)
    assert _severity(d, "still reproduces") == [BLOCK]


def test_a_shipped_key_nothing_tests_blocks(tmp_path):
    delta = _flat(keys=[{"key": "REQ-77", "services": ["billing"], "mentioned_by": []}])
    d = _decide(tmp_path, _world(tmp_path), delta=delta)
    assert _severity(d, "REQ-77 ships untested") == [BLOCK]
    linked = _flat(keys=[{"key": "REQ-1", "services": ["billing"], "mentioned_by": []}])
    assert not _severity(_decide(tmp_path / "2", _world(tmp_path / "2"), delta=linked), "untested")


def test_an_open_blocking_defect_that_ships_blocks(tmp_path):
    delta = _flat(keys=[{"key": "BUG-5", "services": ["billing"], "mentioned_by": ["t"]}])
    defects = defects_provider([{"id": "BUG-5", "severity": "high", "labels": []}], FRESH)
    d = _decide(tmp_path, _world(tmp_path), delta=delta, defects=defects)
    assert BLOCK in _severity(d, "BUG-5 ships but is an open blocking defect")


def test_policy_can_soften_a_change_scope_check(tmp_path):
    pol = policy(**FULL, change_scope={"shipped_untested": "attention"})
    delta = _flat(keys=[{"key": "REQ-77", "services": ["billing"], "mentioned_by": []}])
    d = _decide(tmp_path, _world(tmp_path), delta=delta, pol=pol)
    assert d.verdict is ReleaseVerdict.GO and _severity(d, "ships untested") == ["attention"]


# ---------------------------------------------------------------------- E5 gaps


def test_a_failed_gap_ratchet_blocks_and_lists_the_new_gaps(tmp_path):
    reports = _world(tmp_path)
    (reports / "gaps.json").write_text(
        json.dumps(
            {"ratchet": {"new": ["openapi:POST /api/refunds"], "closed": [], "passed": False}}
        )
    )
    d = _decide(tmp_path, reports)
    assert d.verdict is ReleaseVerdict.NO_GO
    assert _severity(d, "openapi:POST /api/refunds") == [BLOCK]
    soft = _decide(tmp_path, reports, pol=policy(**FULL, coverage_gaps="no_verdict"))
    assert soft.verdict is ReleaseVerdict.NO_VERDICT
    (reports / "gaps.json").unlink()
    ignored = _decide(tmp_path, reports, pol=policy(**FULL, coverage_gaps="ignore"))
    assert ignored.verdict is ReleaseVerdict.GO


# ---------------------------------------------------------------------- E6 snapshot age


def test_an_old_or_undated_snapshot_is_no_verdict(tmp_path):
    reports = _world(tmp_path)
    old = _decide(tmp_path, reports, defects=defects_provider([], "2026-05-01T00:00:00Z"))
    assert old.verdict is ReleaseVerdict.NO_VERDICT
    assert any("days old" in r.message for r in old.reasons if r.criterion == "defects:age")
    undated = _decide(tmp_path, reports, defects=defects_provider([], None))
    assert undated.verdict is ReleaseVerdict.NO_VERDICT
    other = _decide(tmp_path, reports, defects=defects_provider([], "2026-05-31T13:00:00Z"))
    fresh = _decide(tmp_path, reports)
    assert other.inputs_hash != fresh.inputs_hash  # generated_at is part of the inputs


# ---------------------------------------------------------------------- E7 enforcement


def _cli(tmp_path, *extra, env=None):
    reports = write_artifacts(tmp_path / "reports", [proved_run()])
    reg_file = tmp_path / "reg.yaml"
    reg_file.write_text("requirements:\n  - {id: REQ-1, title: one, priority: must}\n")
    out = tmp_path / "out"
    rc = trace_main(
        [
            "release",
            "decide",
            "--release",
            "ci",
            "--candidate",
            "abc123",
            "--reports",
            str(reports),
            "--registry",
            str(reg_file),
            "--waivers",
            str(tmp_path / "none.yaml"),
            "--out",
            str(out),
            "--candidates-dir",
            str(tmp_path / "candidates"),
            *extra,
        ]
    )
    return rc, out


def test_report_enforcement_exits_zero_and_states_it(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(Path(__file__).resolve().parents[2])  # the repository policy
    rc, out = _cli(tmp_path, "--enforcement", "report")
    assert rc == 0
    first = (out / "ci-abc123.md").read_text().splitlines()[0]
    assert first == "RELEASE=NO_VERDICT ENFORCEMENT=REPORT CANDIDATE=abc123"
    doc = json.loads((out / "ci-abc123.json").read_text())
    messages = [r["message"] for r in doc["reasons"]]
    assert any("names no file" in m for m in messages)  # an input problem, listed
    assert any("change scope unknown" in m for m in messages)
    assert "enforcement: report" in capsys.readouterr().out


def test_unstable_warns_and_fail_keeps_the_exit_code(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(Path(__file__).resolve().parents[2])
    monkeypatch.setenv("RELEASE_VERDICT_ENFORCEMENT", "unstable")
    rc, _ = _cli(tmp_path / "u")
    assert (
        rc == 0 and "::warning title=Release verdict::RELEASE=NO_VERDICT" in capsys.readouterr().out
    )
    monkeypatch.delenv("RELEASE_VERDICT_ENFORCEMENT")
    monkeypatch.setenv("RELEASE_VERDICT", "report")
    assert _cli(tmp_path / "r")[0] == 0
    monkeypatch.delenv("RELEASE_VERDICT")
    assert _cli(tmp_path / "f")[0] == 3
