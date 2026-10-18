"""End-of-run status: aggregation, lane headlines, and the plugin under xdist."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from verification.status_report import (
    LANE_ERRORED_BEFORE_ASSERTING,
    LANE_NOTHING_HELD,
    LANE_PROVISIONED_BUT_ASSERTED_NOTHING,
    LANE_PROVISIONED_NOTHING,
    NO_DETECTOR_VERDICT,
    NO_PROOF,
    LaneTally,
    aggregate_coverage,
    aggregate_defects,
    lane_headline,
    render_status,
)

pytest_plugins = ["pytester"]

ROOT = Path(__file__).resolve().parents[2]


def red(ticket: str, verdict: str, detail: str = "") -> dict:
    return {"kind": "expected_red", "ticket": ticket, "verdict": verdict, "detail": detail}


def cov(subject: str, verdict: str) -> dict:
    return {"kind": "coverage", "ticket": None, "subject": subject, "verdict": verdict}


# --- aggregation ------------------------------------------------------------------


def test_defects_aggregate_by_severity_not_arrival_order():
    records = [
        red("BUG-1", "reproduces", "seen on page A"),
        red("BUG-1", "cleared", "absent on page B"),
        red("BUG-2", "cleared"),
        red("BUG-2", "cleared"),
        red("BUG-3", "held_empty"),
        red("BUG-3", "signature_mismatch", "other"),
        red("BUG-4", "closed_pass"),
        red("BUG-4", "closed_regressed"),
        cov("REQ-1", "proved"),
    ]
    out = aggregate_defects(records)
    assert {k: v.verdict for k, v in out.items()} == {
        "BUG-1": "reproduces",  # one late pass must not announce a fix
        "BUG-2": "cleared",  # cleared only when every record cleared
        "BUG-3": "signature_mismatch",
        "BUG-4": "closed_regressed",
    }
    assert out["BUG-1"].detail == "seen on page A" and out["BUG-1"].records == 2


def test_coverage_keeps_the_outcome_closest_to_evidence():
    out = aggregate_coverage(
        [cov("REQ-1", "abstained"), cov("REQ-1", "proved"), cov("REQ-2", "recorded")]
    )
    assert {k: v.verdict for k, v in out.items()} == {"REQ-1": "proved", "REQ-2": "recorded"}


# --- lane headlines -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("failed", "errored", "provisioned", "headline"),
    [
        (2, 1, 0, LANE_NOTHING_HELD),
        (0, 3, 3, LANE_ERRORED_BEFORE_ASSERTING),
        (0, 0, 2, LANE_PROVISIONED_BUT_ASSERTED_NOTHING),
        (0, 0, 0, LANE_PROVISIONED_NOTHING),
    ],
)
def test_lane_headline_names_the_failure_that_happened(failed, errored, provisioned, headline):
    text = lane_headline(3, failed, errored, provisioned)
    assert text.startswith(f"**{headline}**")
    others = {
        LANE_NOTHING_HELD,
        LANE_ERRORED_BEFORE_ASSERTING,
        LANE_PROVISIONED_BUT_ASSERTED_NOTHING,
        LANE_PROVISIONED_NOTHING,
    } - {headline}
    assert not any(o in text for o in others)


def test_lane_tally_collapses_reruns_onto_the_best_attempt():
    lane = LaneTally()
    lane.observe("t::a", "failed", "first attempt")
    lane.observe("t::a", "passed")
    lane.observe("t::a", "aborted", "late, worse")
    lane.observe("t::b", "aborted", "no data", provisioned=True)
    assert lane.outcomes == {"t::a": "passed", "t::b": "aborted"}
    assert lane.headline() is None  # something passed
    only_b = LaneTally()
    only_b.observe("t::b", "aborted", provisioned=True)
    assert LANE_PROVISIONED_BUT_ASSERTED_NOTHING in (only_b.headline() or "")


def test_render_leads_with_the_silent_states():
    text = render_status(
        aggregate_defects([red("BUG-1", "held_empty")]),
        aggregate_coverage([cov("REQ-1", "recorded")]),
    )
    assert NO_DETECTOR_VERDICT in text and NO_PROOF in text
    assert "| BUG-1 | held_empty |" in text
    healthy = render_status(
        aggregate_defects([red("BUG-1", "reproduces")]), aggregate_coverage([cov("R", "proved")])
    )
    assert NO_DETECTOR_VERDICT not in healthy and NO_PROOF not in healthy


def test_render_escapes_table_pipes():
    text = render_status(aggregate_defects([red("BUG-1", "reproduces", "a | b")]), {})
    assert "a \\| b" in text


# --- the plugin, end to end ----------------------------------------------------------

SUITE = textwrap.dedent(
    r"""
    import pytest
    from verification.evidence_verdict import EvidenceVerdict
    from verification.defect_detector import still_reproduces
    from verification.status_report import mark_provisioned

    SIG = r"BUG-142: discount was not applied"

    def broken():
        raise AssertionError("BUG-142: discount was not applied to the total")

    def test_detector_reproduces():
        still_reproduces("BUG-142", SIG, broken, exercised=lambda: None)

    def test_detector_not_exercised():
        def control():
            raise AssertionError("order read back empty")
        still_reproduces("BUG-143", r"BUG-143: refund amount was wrong", broken, exercised=control)

    @pytest.mark.known_defect("BUG-150", raises=AssertionError, match=r"footer shows old text")
    def test_marker_reproduces():
        assert False, "BUG-150: footer shows old text"

    def test_coverage_recorded_only():
        EvidenceVerdict("REQ-9 totals").recorded("tax 2.10")

    @pytest.mark.seeded
    def test_lane_fails_after_provisioning(request):
        mark_provisioned(request.node)
        assert 1 == 2

    @pytest.mark.seeded
    def test_lane_errors(request):
        raise RuntimeError("connection reset")

    @pytest.mark.seeded
    def test_lane_aborts(request):
        mark_provisioned(request.node)
        pytest.skip("no eligible date in range")
    """
)


@pytest.fixture
def run_suite(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    verdicts = tmp_path / "out" / "verdicts.jsonl"
    status = tmp_path / "out" / "status.md"
    monkeypatch.setenv("PYTHONPATH", str(ROOT))
    monkeypatch.setenv("VERDICTS_FILE", str(verdicts))
    monkeypatch.setenv("VERDICT_STATUS_FILE", str(status))
    pytester.makeini("[pytest]\nmarkers =\n    seeded\n")
    pytester.makepyfile(test_suite=SUITE)

    def run(*args: str) -> tuple[pytest.RunResult, Path, Path]:
        result = pytester.runpytest_subprocess(
            "-p", "verification.pytest_plugin", "-p", "no:cacheprovider", "-rA", *args
        )
        return result, verdicts, status

    return run


@pytest.mark.parametrize("workers", [[], ["-n", "2"]], ids=["serial", "xdist"])
def test_plugin_records_once_and_reports_once(run_suite, workers):
    result, verdicts, status = run_suite(*workers)
    result.assert_outcomes(passed=2, failed=2, skipped=2, xfailed=1)
    records = [json.loads(line) for line in verdicts.read_text().splitlines()]
    by_ticket = {(r["ticket"], r["verdict"]) for r in records if r["kind"] == "expected_red"}
    assert by_ticket == {
        ("BUG-142", "reproduces"),
        ("BUG-143", "held_empty"),
        ("BUG-150", "reproduces"),
    }
    assert len(records) == 4  # one line per outcome, never doubled by the controller
    assert all(r["nodeid"].startswith("test_suite.py::") for r in records)
    report = status.read_text()
    assert NO_PROOF in report
    assert LANE_NOTHING_HELD in report
    lane_rows = [line for line in report.splitlines() if line.startswith("| test_suite.py::")]
    assert {row.split(" | ")[1] for row in lane_rows} == {"failed", "errored", "aborted"}


def test_run_id_scopes_the_report_to_this_run(run_suite, monkeypatch):
    run_suite()
    monkeypatch.setenv("TEST_RUN_ID", "second")
    _, verdicts, status = run_suite("-k", "detector_reproduces")
    lines = verdicts.read_text().splitlines()
    assert sum(json.loads(line)["run_id"] == "second" for line in lines) == 1
    report = status.read_text()
    assert "BUG-142" in report and "BUG-143" not in report and "Lane health" not in report


def test_offset_scopes_the_report_without_a_run_id(run_suite):
    run_suite()
    _, _, status = run_suite("-k", "coverage_recorded_only")
    report = status.read_text()
    assert "REQ-9" in report and "BUG-142" not in report


def test_nothing_to_report_writes_nothing(pytester, monkeypatch, tmp_path):
    status = tmp_path / "status.md"
    monkeypatch.setenv("PYTHONPATH", str(ROOT))
    monkeypatch.setenv("VERDICTS_FILE", str(tmp_path / "v.jsonl"))
    monkeypatch.setenv("VERDICT_STATUS_FILE", str(status))
    pytester.makeini("[pytest]\n")
    pytester.makepyfile(test_plain="def test_ok():\n    assert True\n")
    result = pytester.runpytest_subprocess("-p", "verification.pytest_plugin")
    result.assert_outcomes(passed=1)
    assert not status.exists()
