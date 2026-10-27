"""One fixture per matrix status, plus the precedence conflicts that must never overstate."""

from __future__ import annotations

from tests.traceability._runs import RunSpec, T, coverage, red, registry, write_artifacts
from traceability.evidence import ArtifactResultSource
from traceability.matrix import Status, build_matrix, render_markdown

M = "tests/test_m.py::"


def _matrix(tmp_path, runs, *ids):
    write_artifacts(tmp_path, runs)
    return build_matrix(registry(*ids), None, ArtifactResultSource(tmp_path))


def test_every_status(tmp_path):
    good = RunSpec(
        "r1",
        [
            T(M + "t_proved", reqs=("REQ-1",)),
            T(M + "t_passing", reqs=("REQ-2",)),
            T(M + "t_failing", "failed", reqs=("REQ-3",)),
            T(M + "t_detector", reqs=("REQ-4",)),
            T(M + "t_xfail", "xfailed", reqs=("REQ-4",), defect="BUG-7"),
            T(M + "t_draft", reqs=("REQ-5",), draft=True),
            T(M + "t_skipped", "skipped", reqs=("REQ-6",)),
        ],
        verdicts=[
            coverage("r1", M + "t_proved", "REQ-1 totals add up"),
            red("r1", M + "t_detector", "BUG-142"),
        ],
    )
    bad = RunSpec(
        "r2",
        [T(M + "t_unverified", reqs=("REQ-8",))],
        integrity="FAIL",
        lane="browser",
        timestamp="2026-01-01T01:00:00+00:00",
    )
    m = _matrix(tmp_path, [good, bad], *[f"REQ-{i}" for i in range(1, 9)])
    got = {r.requirement_id: r.status for r in m.rows}
    assert got == {
        "REQ-1": Status.PROVED,
        "REQ-2": Status.PASSING,
        "REQ-3": Status.FAILING,
        "REQ-4": Status.KNOWN_DEFECT,
        "REQ-5": Status.DRAFT_ONLY,
        "REQ-6": Status.NOT_RUN,
        "REQ-7": Status.UNCOVERED,
        "REQ-8": Status.UNVERIFIED,
    }
    assert set(m.row("REQ-4").cells[0].defects) | set(m.row("REQ-4").cells[1].defects) == {
        "BUG-142",
        "BUG-7",
    }
    # problems first
    assert [r.status for r in m.rows][:2] == [Status.FAILING, Status.UNCOVERED]


def test_a_pass_from_a_failed_integrity_run_is_unverified_never_passing(tmp_path):
    run = RunSpec("r1", [T(M + "a", reqs=("REQ-1",))], integrity="FAIL")
    assert _matrix(tmp_path, [run], "REQ-1").row("REQ-1").status is Status.UNVERIFIED


def test_a_proved_verdict_from_a_failed_integrity_run_proves_nothing(tmp_path):
    run = RunSpec(
        "r1",
        [T(M + "a", reqs=("REQ-1",))],
        integrity="FAIL",
        verdicts=[coverage("r1", M + "a", "REQ-1 x")],
    )
    assert _matrix(tmp_path, [run], "REQ-1").row("REQ-1").status is Status.UNVERIFIED


def test_one_failing_case_outweighs_passing_ones(tmp_path):
    run = RunSpec(
        "r1",
        [T(M + "a", reqs=("REQ-1",)), T(M + "b", "error", reqs=("REQ-1",))],
        verdicts=[coverage("r1", M + "a", "REQ-1 x")],
    )
    assert _matrix(tmp_path, [run], "REQ-1").row("REQ-1").status is Status.FAILING


def test_a_mismatched_or_regressed_detector_is_failing_not_known(tmp_path):
    for verdict in ("signature_mismatch", "closed_regressed"):
        root = tmp_path / verdict
        run = RunSpec(
            "r1",
            [T(M + "a", "failed", reqs=("REQ-1",))],
            verdicts=[red("r1", M + "a", "BUG-1", verdict)],
        )
        assert _matrix(root, [run], "REQ-1").row("REQ-1").status is Status.FAILING


def test_a_cleared_detector_is_fix_observed_not_failing(tmp_path):
    # Was FAILING before: a detector that stops reproducing is a fix to verify, not a failure.
    run = RunSpec(
        "r1",
        [T(M + "a", "failed", reqs=("REQ-1",))],
        verdicts=[red("r1", M + "a", "BUG-1", "cleared")],
    )
    m = _matrix(tmp_path, [run], "REQ-1")
    assert m.row("REQ-1").status is Status.FIX_OBSERVED
    assert m.row("REQ-1").cells[0].state == "fix_observed"
    assert "detector cleared: verify, then remove the wrapper" in render_markdown(m)


def test_a_real_failure_outranks_a_cleared_detector(tmp_path):
    run = RunSpec(
        "r1",
        [T(M + "a", "failed", reqs=("REQ-1",)), T(M + "b", "failed", reqs=("REQ-1",))],
        verdicts=[red("r1", M + "a", "BUG-1", "cleared")],
    )
    assert _matrix(tmp_path, [run], "REQ-1").row("REQ-1").status is Status.FAILING


def test_a_pass_after_a_failed_attempt_is_flaky_and_keeps_the_first_message(tmp_path):
    run = RunSpec(
        "r1",
        [T(M + "a", reqs=("REQ-1",), failed_first="AssertionError: total was 9.99")],
        verdicts=[coverage("r1", M + "a", "REQ-1 x")],
    )
    m = _matrix(tmp_path, [run], "REQ-1")
    assert m.row("REQ-1").status is Status.FLAKY
    assert "AssertionError: total was 9.99" in render_markdown(m)


def test_flaky_ranks_below_failing_and_above_known_defect(tmp_path):
    flaky = T(M + "a", reqs=("REQ-1",), failed_first="boom")
    known = T(M + "b", "xfailed", reqs=("REQ-1",), defect="BUG-2")
    run = RunSpec("r1", [flaky, known])
    assert _matrix(tmp_path / "1", [run], "REQ-1").row("REQ-1").status is Status.FLAKY
    run = RunSpec("r1", [flaky, T(M + "c", "failed", reqs=("REQ-1",))])
    assert _matrix(tmp_path / "2", [run], "REQ-1").row("REQ-1").status is Status.FAILING
    order = list(Status)
    assert order.index(Status.FAILING) < order.index(Status.FIX_OBSERVED)
    assert order.index(Status.FIX_OBSERVED) < order.index(Status.KNOWN_DEFECT)
    assert order.index(Status.FLAKY) < order.index(Status.KNOWN_DEFECT)


def test_a_recorded_coverage_verdict_is_not_proof(tmp_path):
    run = RunSpec(
        "r1",
        [T(M + "a", reqs=("REQ-1",))],
        verdicts=[coverage("r1", M + "a", "REQ-1 x", "recorded")],
    )
    assert _matrix(tmp_path, [run], "REQ-1").row("REQ-1").status is Status.PASSING


def test_a_proved_draft_is_still_draft_only(tmp_path):
    run = RunSpec(
        "r1",
        [T(M + "a", reqs=("REQ-1",), draft=True)],
        verdicts=[coverage("r1", M + "a", "REQ-1 x")],
    )
    assert _matrix(tmp_path, [run], "REQ-1").row("REQ-1").status is Status.DRAFT_ONLY


def test_uncovered_even_when_the_run_is_empty(tmp_path):
    m = _matrix(tmp_path, [RunSpec("r1", [])], "REQ-1")
    assert m.row("REQ-1").status is Status.UNCOVERED


def test_a_newer_skip_keeps_the_older_executed_answer(tmp_path):
    old = RunSpec("r1", [T(M + "a", "failed", reqs=("REQ-1",))])
    new = RunSpec(
        "r2", [T(M + "a", "skipped", reqs=("REQ-1",))], timestamp="2026-02-01T00:00:00+00:00"
    )
    assert _matrix(tmp_path, [old, new], "REQ-1").row("REQ-1").status is Status.FAILING


def test_the_newest_executed_result_wins(tmp_path):
    old = RunSpec("r1", [T(M + "a", "failed", reqs=("REQ-1",))])
    new = RunSpec("r2", [T(M + "a", reqs=("REQ-1",))], timestamp="2026-02-01T00:00:00+00:00")
    assert _matrix(tmp_path, [old, new], "REQ-1").row("REQ-1").status is Status.PASSING


def test_links_to_unregistered_ids_are_listed(tmp_path):
    run = RunSpec("r1", [T(M + "a", reqs=("REQ-1", "REQ-99"))])
    m = _matrix(tmp_path, [run], "REQ-1")
    assert m.unregistered == [{"requirement_id": "REQ-99", "nodeids": [M + "a"]}]
