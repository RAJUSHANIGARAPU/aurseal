"""One case per composition rule of the release verdict."""

from __future__ import annotations

from tests.traceability._release import M, decide, policy, proved_run, reg
from tests.traceability._runs import RunSpec, T, coverage, red
from traceability.release import BLOCK, NO_VERDICT, ReleaseVerdict


def _severities(decision, severity):
    return [r.criterion for r in decision.reasons if r.severity == severity]


def test_a_proved_must_have_in_a_sound_run_is_go(tmp_path):
    d = decide(tmp_path, [proved_run()])
    assert d.verdict is ReleaseVerdict.GO and d.evidence_runs == ["r1"]


def test_no_run_at_all_is_no_verdict_never_go(tmp_path):
    d = decide(tmp_path, [])
    assert d.verdict is ReleaseVerdict.NO_VERDICT
    assert "integrity" in _severities(d, NO_VERDICT)


def test_a_passing_run_with_failed_integrity_is_ignored(tmp_path):
    d = decide(tmp_path, [proved_run(integrity="FAIL")])
    assert d.verdict is ReleaseVerdict.NO_VERDICT
    assert any("not evidence" in r.message for r in d.reasons if r.kind == "integrity")


def test_failed_integrity_run_beside_a_sound_one_is_only_attention(tmp_path):
    bad = proved_run("r2", integrity="FAIL", lane="browser")
    d = decide(tmp_path, [proved_run(), bad])
    assert d.verdict is ReleaseVerdict.GO and d.evidence_runs == ["r1"]


def test_signature_mismatch_blocks(tmp_path):
    run = proved_run()
    run.tests.append(T(M + "d", "failed"))
    run.verdicts.append(red("r1", M + "d", "BUG-9", "signature_mismatch"))
    d = decide(tmp_path, [run])
    assert d.verdict is ReleaseVerdict.NO_GO and "detector:BUG-9" in _severities(d, BLOCK)


def test_closed_regressed_blocks(tmp_path):
    run = proved_run()
    run.verdicts.append(red("r1", M + "a", "BUG-9", "closed_regressed"))
    assert decide(tmp_path, [run]).verdict is ReleaseVerdict.NO_GO


def test_cleared_is_attention_not_a_blocker(tmp_path):
    run = proved_run()
    run.tests.append(T(M + "d", "failed"))
    run.verdicts.append(red("r1", M + "d", "BUG-9", "cleared"))
    d = decide(tmp_path, [run])
    assert d.verdict is ReleaseVerdict.GO
    assert any(r.severity == "attention" and r.criterion == "detector:BUG-9" for r in d.reasons)


def test_an_unexercised_detector_is_no_verdict(tmp_path):
    run = proved_run()
    run.verdicts.append(red("r1", M + "z", "BUG-9", "held_empty"))
    assert decide(tmp_path, [run]).verdict is ReleaseVerdict.NO_VERDICT


def test_strict_policy_passing_must_have_is_no_verdict(tmp_path):
    run = RunSpec("r1", [T(M + "a", reqs=("REQ-1",))])
    d = decide(tmp_path, [run])
    assert d.verdict is ReleaseVerdict.NO_VERDICT
    assert "requirement:REQ-1" in _severities(d, NO_VERDICT)


def test_a_passing_should_have_is_enough(tmp_path):
    run = RunSpec(
        "r1",
        [T(M + "a", reqs=("REQ-1",)), T(M + "b", reqs=("REQ-2",))],
        verdicts=[coverage("r1", M + "a", "REQ-1 x")],
    )
    d = decide(tmp_path, [run], registry=reg(("REQ-1", "must"), ("REQ-2", "should")))
    assert d.verdict is ReleaseVerdict.GO


def test_a_failing_must_have_blocks(tmp_path):
    run = RunSpec("r1", [T(M + "a", "failed", reqs=("REQ-1",))])
    assert decide(tmp_path, [run]).verdict is ReleaseVerdict.NO_GO


def test_a_missing_priority_falls_back_to_the_default(tmp_path):
    run = RunSpec("r1", [T(M + "a", reqs=("REQ-1",))])
    d = decide(tmp_path, [run], registry=reg(("REQ-1", None)))
    assert d.verdict is ReleaseVerdict.NO_VERDICT  # default_priority must, strict


def test_defects_checked_without_a_tracker_is_no_verdict(tmp_path):
    pol = policy(defects={"blocking_severities": ["high"]})
    assert decide(tmp_path, [proved_run()], pol=pol).verdict is ReleaseVerdict.NO_VERDICT


def test_open_defects(tmp_path):
    pol = policy(defects={"blocking_severities": ["high"], "blocking_labels": ["blocker"]})

    def low():
        return [{"id": "GH-1", "severity": "low", "labels": []}]

    def high():
        return [{"id": "GH-2", "severity": "high", "labels": [], "title": "totals off"}]

    def labelled():
        return [{"id": "GH-3", "severity": None, "labels": ["blocker"]}]

    def down():
        raise ConnectionError("no route")

    assert decide(tmp_path, [proved_run()], pol=pol, open_defects=low).verdict is (
        ReleaseVerdict.GO
    )
    d = decide(tmp_path / "2", [proved_run()], pol=pol, open_defects=high)
    assert d.verdict is ReleaseVerdict.NO_GO and "defect:GH-2" in _severities(d, BLOCK)
    assert decide(tmp_path / "3", [proved_run()], pol=pol, open_defects=labelled).verdict is (
        ReleaseVerdict.NO_GO
    )
    assert decide(tmp_path / "4", [proved_run()], pol=pol, open_defects=down).verdict is (
        ReleaseVerdict.NO_VERDICT
    )


def test_plan_criteria_fold_in(tmp_path):
    from traceability.plans import load_plan

    path = tmp_path / "p1.yaml"
    path.write_text(
        "id: p1\nscope:\n  include:\n    - requirements: [REQ-1]\n"
        "exit_criteria:\n  min_pass_rate: 1.0\n"
    )
    run = proved_run()
    run.tests.append(T(M + "other", "failed"))
    d = decide(tmp_path, [run], plan=load_plan(path))
    assert d.verdict is ReleaseVerdict.NO_GO and "plan:min_pass_rate" in _severities(d, BLOCK)


def test_an_unknown_verdict_kind_is_reported(tmp_path):
    run = proved_run()
    run.verdicts.append(
        {
            "run_id": "r1",
            "nodeid": M + "a",
            "kind": "performance",
            "verdict": "slow",
            "ticket": None,
            "subject": None,
        }
    )
    d = decide(tmp_path, [run])
    assert any(r.criterion == "verdict-kind:performance" for r in d.reasons)
