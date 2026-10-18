"""Run-trust rules, summary and markers, driven offline from tallies."""

from __future__ import annotations

import io
import json

import pytest

from gate.integrity import (
    VERDICT_LINE_EMPTY,
    VERDICT_LINE_FAIL,
    VERDICT_LINE_PASS,
    Enforcement,
    Outcome,
    RunTally,
    RunTrustGate,
    Silence,
    TestResult,
    Thresholds,
    Verdict,
    build_tally,
    check_marker,
    earlier_invocation_results,
    main,
    marker_paths,
    read_marker,
    write_markers,
)
from gate.provenance import FixtureRow
from gate.verdicts import VerdictRow


def _tally(selected=20, executed=20, skipped=0, **kw) -> RunTally:
    return RunTally(selected=selected, executed=executed, skipped=skipped, **kw)


def _rules(result) -> list[str]:
    return [b.rule for b in result.breaches]


# --------------------------------------------------------------------- rule 1


def test_executed_floor_fails_a_shrunken_run():
    result = RunTrustGate().evaluate(_tally(selected=25, executed=6, skipped=19))
    assert result.verdict is Verdict.FAIL
    assert "EXECUTED FLOOR" in _rules(result)


def test_executed_floor_holds_at_exactly_the_floor():
    result = RunTrustGate().evaluate(_tally(selected=25, executed=10, skipped=0))
    assert "EXECUTED FLOOR" not in _rules(result)


def test_narrow_selection_is_not_judged_by_the_floor():
    result = RunTrustGate().evaluate(_tally(selected=6, executed=6))
    assert result.verdict is Verdict.PASS
    assert any("narrowed selection" in n for n in result.not_applicable)


def test_floor_disabled_at_zero():
    gate = RunTrustGate(Thresholds(min_executed=0, max_skip_ratio=1.0))
    result = gate.evaluate(_tally(selected=25, executed=1, skipped=0))
    assert result.verdict is Verdict.UNJUDGED
    assert result.headline == VERDICT_LINE_EMPTY


# --------------------------------------------------------------------- rule 2


def test_skip_ratio_is_independent_of_the_floor():
    # Plenty executed, still mostly silence.
    result = RunTrustGate().evaluate(_tally(selected=100, executed=40, skipped=60))
    assert _rules(result) == ["SKIP RATIO"]


def test_skip_ratio_at_the_bar_holds():
    result = RunTrustGate().evaluate(_tally(selected=20, executed=10, skipped=10))
    assert result.verdict is Verdict.PASS


def test_skip_ratio_disabled_at_one_and_on_empty_selection():
    gate = RunTrustGate(Thresholds(max_skip_ratio=1.0))
    assert "SKIP RATIO" not in _rules(gate.evaluate(_tally(selected=4, executed=0, skipped=4)))
    empty = RunTrustGate().evaluate(_tally(selected=0, executed=0))
    assert empty.verdict is Verdict.UNJUDGED


# --------------------------------------------------------------------- rule 3


def test_detectors_selected_without_a_verdict_fail():
    result = RunTrustGate().evaluate(
        _tally(selected=4, executed=0, skipped=4, detectors_selected=4, verdicts_reached=0)
    )
    assert "DETECTOR VERDICTS" in _rules(result)


def test_detector_rule_absent_when_no_detector_selected():
    result = RunTrustGate().evaluate(_tally())
    assert any(n.startswith("detector verdicts - no detector") for n in result.not_applicable)


def test_one_verdict_satisfies_the_detector_rule():
    result = RunTrustGate().evaluate(_tally(detectors_selected=3, verdicts_reached=1))
    assert result.verdict is Verdict.PASS


# --------------------------------------------------------------------- rule 4


def test_seeded_lane_with_nothing_on_acquired_data_fails():
    result = RunTrustGate().evaluate(_tally(seeded_selected=5, passed_on_acquired=0))
    assert _rules(result) == ["FIXTURE PROVENANCE"]


def test_provenance_rule_disabled_at_zero():
    gate = RunTrustGate(Thresholds(min_acquired=0))
    assert gate.evaluate(_tally(seeded_selected=5)).verdict is Verdict.PASS


# --------------------------------------------------------------- enforcement


@pytest.mark.parametrize(
    ("enforcement", "fails", "unsettles"),
    [
        (Enforcement.FAIL, True, False),
        (Enforcement.UNSTABLE, False, True),
        (Enforcement.REPORT, False, False),
    ],
)
def test_enforcement_never_changes_the_verdict(enforcement, fails, unsettles):
    gate = RunTrustGate(Thresholds(enforcement=enforcement))
    result = gate.evaluate(_tally(selected=25, executed=2, skipped=23))
    assert result.verdict is Verdict.FAIL
    assert result.headline == VERDICT_LINE_FAIL
    assert (result.fails_the_build, result.unsettles_the_build) == (fails, unsettles)


def test_thresholds_from_env_fall_back_on_junk():
    t = Thresholds.from_env(
        {
            "RUN_TRUST_MIN_EXECUTED": "ten",
            "RUN_TRUST_MAX_SKIP_RATIO": "0.25",
            "RUN_TRUST_MIN_VERDICTS": "",
            "RUN_TRUST": "UNSTABLE",
        }
    )
    assert t == Thresholds(min_executed=10, max_skip_ratio=0.25, enforcement=Enforcement.UNSTABLE)
    assert Thresholds.from_env({"RUN_TRUST": "loud"}).enforcement is Enforcement.FAIL


# ------------------------------------------------------------------- summary


def test_summary_names_breaches_the_setting_and_wraps_reasons():
    reason = "the precondition could not be arranged because " + "the shared queue was busy " * 8
    tally = _tally(
        selected=25,
        executed=6,
        skipped=19,
        silences=tuple(Silence(f"t::{i}", "skipped", reason) for i in range(15)),
    )
    gate = RunTrustGate()
    box = gate.summary(tally, gate.evaluate(tally))
    text = "\n".join(box)
    assert VERDICT_LINE_FAIL in box[1]
    assert "RUN_TRUST_MIN_EXECUTED" in text
    assert "... and 3 more" in text
    # Wrapped, not truncated: the reason's last words survive.
    assert text.count("shared queue was busy") >= 8
    assert {len(line) for line in box} == {102}


def test_pass_summary_says_so():
    gate = RunTrustGate()
    box = gate.summary(_tally(), gate.evaluate(_tally()))
    assert VERDICT_LINE_PASS in box[1]


# --------------------------------------------------------------------- tally


def _t(nodeid, outcome, *markers, **kw) -> TestResult:
    return TestResult(nodeid, outcome, tuple(markers), **kw)


def test_build_tally_counts_answers_and_silences():
    tests = [
        _t("a", Outcome.PASSED),
        _t("b", Outcome.FAILED),
        _t("c", Outcome.ERROR),
        _t("d", Outcome.XFAILED, "known_defect", defect="BUG-1"),
        _t("e", Outcome.SKIPPED, reason="no data"),
        _t("f", Outcome.NOT_RUN),
    ]
    tally = build_tally(tests)
    assert (tally.selected, tally.executed, tally.skipped) == (6, 4, 2)
    assert [s.test for s in tally.silences] == ["e", "f"]
    assert (tally.detectors_selected, tally.verdicts_reached) == (1, 1)


def test_verdict_rows_decide_detectors_and_dedupe_by_ticket():
    rows = [
        VerdictRow("r", "x::a", "expected_red", "BUG-7", "reproduces"),
        VerdictRow("r", "x::b", "expected_red", "BUG-7", "cleared"),
        VerdictRow("r", "x::c", "expected_red", None, "held_empty", "no data"),
        VerdictRow("r", "x::d", "expected_red", "BUG-9", "signature_mismatch"),
        VerdictRow("r", "x::e", "coverage", None, "proved"),
    ]
    tally = build_tally([], rows)
    assert tally.detectors_selected == 4
    assert tally.verdicts_reached == 1
    assert {s.outcome for s in tally.detector_silences} == {"held_empty", "signature_mismatch"}


def test_skipped_detector_without_rows_reaches_nothing():
    tally = build_tally([_t("d", Outcome.SKIPPED, "known_defect", reason="env down")])
    assert (tally.detectors_selected, tally.verdicts_reached) == (1, 0)
    assert tally.detector_silences[0].reason == "env down"


def test_cleared_expected_red_counts_as_a_verdict():
    tally = build_tally([_t("d", Outcome.FAILED, "known_defect", defect="BUG-3", cleared=True)])
    assert tally.verdicts_reached == 1


def test_provenance_counts_passing_tests_under_a_covering_row():
    tests = [
        _t("m.py::T::test_a", Outcome.PASSED, "seeded"),
        _t("m.py::T::test_b", Outcome.FAILED, "seeded"),
        _t("n.py::test_c", Outcome.PASSED, "seeded"),
        _t("m.py::Tx::test_d", Outcome.PASSED),
    ]
    rows = [FixtureRow("r", "m.py::T", "fresh", "orders/basic.json")]
    tally = build_tally(tests, fixture_rows=rows)
    assert tally.seeded_selected == 3
    # test_b failed, test_c has no row, Tx is not under T.
    assert tally.passed_on_acquired == 1


def test_custom_marker_names():
    tally = build_tally(
        [_t("a", Outcome.XFAILED, "detector")],
        detector_markers=("detector",),
        seeded_markers=("needs_data",),
    )
    assert tally.detectors_selected == 1


# ------------------------------------------------------------------- markers


def _write(tmp_path, run_id="run-1", lane="default", tests=None):
    tests = tests or [_t("a", Outcome.PASSED), _t("b", Outcome.SKIPPED, reason="r")]
    gate = RunTrustGate(Thresholds(min_executed=1))
    tally = build_tally(tests)
    result = gate.evaluate(tally)
    assert write_markers(
        tmp_path, run_id=run_id, lane=lane, tests=tests, tally=tally, result=result, gate=gate
    )
    return result


def test_markers_first_line_is_machine_readable(tmp_path):
    _write(tmp_path)
    text, json_path = marker_paths(tmp_path)
    first = text.read_text().splitlines()[0]
    assert first == "VERDICT=PASS ENFORCEMENT=FAIL RUN=run-1 LANE=default"
    body = json.loads(json_path.read_text())
    assert body["selected"] == 2 and body["tests"]["b"]["reason"] == "r"


def test_lane_gets_its_own_marker(tmp_path):
    _write(tmp_path, lane="unit")
    assert marker_paths(tmp_path, "unit")[0].name == "run-trust-unit.txt"
    assert read_marker(tmp_path) is None
    assert read_marker(tmp_path, "unit")["lane"] == "unit"


def test_write_failure_is_swallowed(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")
    gate = RunTrustGate()
    tally = build_tally([])
    assert not write_markers(
        blocker, run_id="r", lane="default", tests=[], tally=tally,
        result=gate.evaluate(tally), gate=gate,
    )  # fmt: skip


def test_earlier_invocation_is_only_read_back_for_the_same_run_and_lane(tmp_path):
    _write(tmp_path, run_id="run-1")
    assert set(earlier_invocation_results(tmp_path, "run-1")) == {"a", "b"}
    assert earlier_invocation_results(tmp_path, "run-2") == {}
    assert earlier_invocation_results(tmp_path, "run-1", lane="unit") == {}
    marker_paths(tmp_path)[1].write_text("{not json")
    assert earlier_invocation_results(tmp_path, "run-1") == {}


# ----------------------------------------------------------------------- CLI


def _fail_marker(tmp_path, enforcement: Enforcement):
    gate = RunTrustGate(Thresholds(min_executed=5, enforcement=enforcement))
    tests = [_t(str(i), Outcome.SKIPPED) for i in range(6)]
    tally = build_tally(tests)
    write_markers(
        tmp_path, run_id="r", lane="default", tests=tests, tally=tally,
        result=gate.evaluate(tally), gate=gate,
    )  # fmt: skip


def test_check_marker_exit_codes(tmp_path, monkeypatch):
    out = io.StringIO()
    assert check_marker(tmp_path, out=out) == 2
    assert "the gate did not run" in out.getvalue()
    assert check_marker(tmp_path, require=False, out=io.StringIO()) == 0

    _fail_marker(tmp_path, Enforcement.FAIL)
    out = io.StringIO()
    assert check_marker(tmp_path, out=out) == 1
    assert VERDICT_LINE_FAIL in out.getvalue()

    _fail_marker(tmp_path, Enforcement.UNSTABLE)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    out = io.StringIO()
    assert check_marker(tmp_path, out=out) == 0
    assert "::warning title=Run trust::" in out.getvalue()

    _fail_marker(tmp_path, Enforcement.REPORT)
    assert check_marker(tmp_path, out=io.StringIO()) == 0


def test_main_reads_dir_and_lane(tmp_path, capsys):
    _write(tmp_path, lane="unit")
    assert main(["--dir", str(tmp_path), "--lane", "unit"]) == 0
    assert main(["--dir", str(tmp_path)]) == 2
    assert main(["--dir", str(tmp_path), "--allow-missing"]) == 0
