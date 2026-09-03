"""Tests for autonomous_ui.flakiness.detector."""

from __future__ import annotations

from pathlib import Path

import pytest

from autonomous_ui.flakiness.detector import FlakinessDetector
from autonomous_ui.flakiness.history_store import HistoryStore
from autonomous_ui.flakiness.models import (
    MIN_RUNS,
    FlakRecord,
)


def _rec(test_id: str = "test_x", outcome: str = "passed", worker: str = "main") -> FlakRecord:
    return FlakRecord(
        test_id=test_id,
        run_id="run1",
        outcome=outcome,
        duration_s=1.5,
        error="TimeoutError" if outcome == "failed" else "",
        timestamp="2026-04-25T00:00:00Z",
        worker=worker,
        environment="qa",
    )


def _records(passed: int, failed: int, test_id: str = "test_x") -> list[FlakRecord]:
    return [_rec(test_id, "passed") for _ in range(passed)] + [
        _rec(test_id, "failed") for _ in range(failed)
    ]


@pytest.fixture()
def store(tmp_path: Path) -> HistoryStore:
    return HistoryStore(store_path=tmp_path / "history.jsonl")


@pytest.fixture()
def detector(store: HistoryStore) -> FlakinessDetector:
    return FlakinessDetector(store, min_runs=MIN_RUNS)


# ------------------------------------------------------------------
# compute_profile()
# ------------------------------------------------------------------


def test_compute_profile_flakiness_rate(detector: FlakinessDetector) -> None:
    records = _records(passed=8, failed=2)
    profile = detector.compute_profile("test_x", records)
    assert profile.flakiness_rate == pytest.approx(0.2, abs=1e-6)


def test_compute_profile_is_flaky_true(detector: FlakinessDetector) -> None:
    records = _records(passed=8, failed=2)  # 20% failure rate > FLAKY_MIN_RATE
    profile = detector.compute_profile("test_x", records)
    assert profile.is_flaky is True


def test_compute_profile_always_failing_not_flaky(detector: FlakinessDetector) -> None:
    # A test failing 100% of the time is broken, not flaky
    records = _records(passed=0, failed=10)
    profile = detector.compute_profile("test_x", records)
    assert profile.is_flaky is False
    assert profile.flakiness_rate == pytest.approx(1.0, abs=1e-6)


def test_compute_profile_below_min_rate_not_flaky(detector: FlakinessDetector) -> None:
    # 1 failure in 200 = 0.5% < FLAKY_MIN_RATE
    records = _records(passed=199, failed=1)
    profile = detector.compute_profile("test_x", records)
    assert profile.is_flaky is False


def test_compute_profile_confidence_grows_with_runs(detector: FlakinessDetector) -> None:
    few_runs = _records(passed=2, failed=1)
    many_runs = _records(passed=40, failed=10)
    assert detector.compute_profile("test_x", few_runs).confidence < 1.0
    assert detector.compute_profile("test_x", many_runs).confidence == pytest.approx(1.0)


def test_compute_profile_zero_runs_safe(detector: FlakinessDetector) -> None:
    profile = detector.compute_profile("test_x", [])
    assert profile.total_runs == 0
    assert profile.flakiness_rate == 0.0
    assert profile.is_flaky is False


def test_compute_profile_most_common_error(detector: FlakinessDetector) -> None:
    records = [
        _rec("test_x", "failed"),
        _rec("test_x", "failed"),
        _rec("test_x", "passed"),
        _rec("test_x", "passed"),
        _rec("test_x", "passed"),
    ]
    profile = detector.compute_profile("test_x", records)
    assert profile.most_common_error == "TimeoutError"


def test_compute_profile_avg_duration(detector: FlakinessDetector) -> None:
    records = [
        FlakRecord("t", "r", "passed", 2.0, "", "ts", "main", "qa"),
        FlakRecord("t", "r", "passed", 4.0, "", "ts", "main", "qa"),
    ]
    profile = detector.compute_profile("t", records)
    assert profile.avg_duration_s == pytest.approx(3.0, abs=1e-6)


def test_compute_profile_max_consecutive_failures(detector: FlakinessDetector) -> None:
    records = [
        _rec("t", "passed"),
        _rec("t", "failed"),
        _rec("t", "failed"),
        _rec("t", "failed"),
        _rec("t", "passed"),
        _rec("t", "failed"),
    ]
    profile = detector.compute_profile("t", records)
    assert profile.max_consecutive_failures == 3


# ------------------------------------------------------------------
# get_profiles() and get_flaky_tests()
# ------------------------------------------------------------------


def test_get_profiles_excludes_tests_with_too_few_runs(
    store: HistoryStore, detector: FlakinessDetector
) -> None:
    for rec in _records(passed=2, failed=1, test_id="test_sparse"):
        store.record(rec)
    profiles = detector.get_profiles()
    assert all(p.test_id != "test_sparse" for p in profiles)


def test_get_profiles_sorted_by_rate_descending(
    store: HistoryStore, detector: FlakinessDetector
) -> None:
    for rec in _records(passed=8, failed=2, test_id="test_low"):
        store.record(rec)
    for rec in _records(passed=3, failed=7, test_id="test_high"):
        store.record(rec)
    profiles = detector.get_profiles()
    rates = [p.flakiness_rate for p in profiles]
    assert rates == sorted(rates, reverse=True)


def test_get_flaky_tests_filters_stable(store: HistoryStore, detector: FlakinessDetector) -> None:
    for rec in _records(passed=10, failed=0, test_id="test_stable"):
        store.record(rec)
    for rec in _records(passed=7, failed=3, test_id="test_flaky"):
        store.record(rec)
    flaky = detector.get_flaky_tests()
    ids = [p.test_id for p in flaky]
    assert "test_flaky" in ids
    assert "test_stable" not in ids


# ------------------------------------------------------------------
# severity property
# ------------------------------------------------------------------


def test_severity_high(detector: FlakinessDetector) -> None:
    profile = detector.compute_profile("t", _records(passed=4, failed=6))
    assert profile.severity == "high"


def test_severity_medium(detector: FlakinessDetector) -> None:
    profile = detector.compute_profile("t", _records(passed=9, failed=1))
    assert profile.severity == "medium"


def test_severity_low(detector: FlakinessDetector) -> None:
    # Just above FLAKY_MIN_RATE
    records = _records(passed=97, failed=3, test_id="t")
    profile = detector.compute_profile("t", records)
    # 3% is medium or low depending on exact threshold
    assert profile.severity in ("low", "medium")


# ------------------------------------------------------------------
# Reruns — a failed attempt plus a passing retry is ONE flaky run
# ------------------------------------------------------------------


def _rerun(test_id: str = "test_x") -> FlakRecord:
    """A failed attempt that pytest-rerunfailures will retry."""
    rec = _rec(test_id, "rerun")
    rec.error = "TimeoutError"
    return rec


def _rerun_then_pass(n: int, test_id: str = "test_x") -> list[FlakRecord]:
    """*n* invocations that each failed once and passed on the retry."""
    out: list[FlakRecord] = []
    for _ in range(n):
        out += [_rerun(test_id), _rec(test_id, "passed")]
    return out


def _rerun_until_failed(n: int, attempts: int = 3, test_id: str = "test_x") -> list[FlakRecord]:
    """*n* invocations that exhausted every retry and still failed."""
    out: list[FlakRecord] = []
    for _ in range(n):
        out += [_rerun(test_id) for _ in range(attempts - 1)]
        out.append(_rec(test_id, "failed"))
    return out


def test_fail_then_pass_on_rerun_is_flaky(detector: FlakinessDetector) -> None:
    # THE headline case. On the unfixed code a rerun landed on "skipped", so
    # this history read as ['skipped','passed'] x5 — failure_count 0, is_flaky
    # False, forever. The subsystem was blind to exactly what it exists to find.
    profile = detector.compute_profile("test_x", _rerun_then_pass(MIN_RUNS))
    assert profile.is_flaky is True


def test_fail_then_pass_on_rerun_counts_one_run_per_invocation(
    detector: FlakinessDetector,
) -> None:
    profile = detector.compute_profile("test_x", _rerun_then_pass(MIN_RUNS))
    assert profile.total_runs == MIN_RUNS
    assert profile.flaky_pass_count == MIN_RUNS


def test_fail_then_pass_on_rerun_is_high_severity(detector: FlakinessDetector) -> None:
    # It misbehaved on every single invocation; "low" would bury it.
    profile = detector.compute_profile("test_x", _rerun_then_pass(MIN_RUNS))
    assert profile.severity == "high"


def test_exhausted_reruns_are_not_flaky(detector: FlakinessDetector) -> None:
    # Measured on the unfixed code: ['skipped','skipped','failed'] gave rate
    # 0.33, and after two invocations total=6 >= MIN_RUNS with confidence 1.0 —
    # a permanently broken test reported as flaky and handed "add an explicit
    # wait". It is broken; say so by NOT calling it flaky.
    profile = detector.compute_profile("test_x", _rerun_until_failed(MIN_RUNS))
    assert profile.is_flaky is False
    assert profile.flakiness_rate == pytest.approx(1.0, abs=1e-6)


def test_exhausted_reruns_count_one_run_and_one_failure_each(
    detector: FlakinessDetector,
) -> None:
    profile = detector.compute_profile("test_x", _rerun_until_failed(4))
    assert profile.total_runs == 4
    assert profile.failure_count == 4


def test_rerun_attempt_error_reaches_the_profile(detector: FlakinessDetector) -> None:
    # The passing retry carries no error text; the failed attempt is the only
    # source, and PatternAnalyzer classifies from it.
    profile = detector.compute_profile("test_x", _rerun_then_pass(MIN_RUNS))
    assert profile.most_common_error == "TimeoutError"


def test_flaky_pass_does_not_extend_a_failure_streak(detector: FlakinessDetector) -> None:
    records = (
        _rerun_until_failed(2)  # two hard failures
        + _rerun_then_pass(1)  # recovered on retry — the streak ends here
        + _rerun_until_failed(1)
    )
    profile = detector.compute_profile("test_x", records)
    assert profile.max_consecutive_failures == 2


def test_trailing_rerun_without_a_verdict_is_not_counted(detector: FlakinessDetector) -> None:
    # The session died mid-retry. There is no final outcome, so there is no run.
    records = _records(passed=4, failed=1) + [_rec("test_x", "rerun")]
    profile = detector.compute_profile("test_x", records)
    assert profile.total_runs == 5


def test_plain_history_is_unaffected_by_rerun_handling(detector: FlakinessDetector) -> None:
    # Positive control: history with no reruns must collapse 1:1 to runs.
    profile = detector.compute_profile("test_x", _records(passed=8, failed=2))
    assert (profile.total_runs, profile.failure_count) == (10, 2)
    assert profile.is_flaky is True


# ------------------------------------------------------------------
# Skips must not fill the denominator
# ------------------------------------------------------------------


def test_one_skip_does_not_rescue_an_always_failing_test(detector: FlakinessDetector) -> None:
    # Measured on the unfixed code: 9 failed + 1 skipped gave rate 0.90,
    # is_flaky True, severity high, confidence 1.0 — one skip was enough to
    # duck ALWAYS_FAIL_THRESHOLD and get a broken test filed as flaky.
    records = _records(passed=0, failed=9) + [_rec("test_x", "skipped")]
    profile = detector.compute_profile("test_x", records)
    assert profile.total_runs == 9
    assert profile.flakiness_rate == pytest.approx(1.0, abs=1e-6)
    assert profile.is_flaky is False


def test_nine_failures_and_one_pass_is_still_flaky(detector: FlakinessDetector) -> None:
    # Positive control for the test above: the same shape with a real PASS
    # instead of a skip is genuine 90% flakiness and must stay flagged, high.
    records = _records(passed=1, failed=9)
    profile = detector.compute_profile("test_x", records)
    assert profile.is_flaky is True
    assert profile.severity == "high"


def test_skips_do_not_manufacture_confidence(detector: FlakinessDetector) -> None:
    # Measured: 49 skipped + 1 failed gave rate 0.020, is_flaky True,
    # confidence 1.0 — a flaky verdict from a single execution.
    records = [_rec("test_x", "skipped") for _ in range(49)] + [_rec("test_x", "failed")]
    profile = detector.compute_profile("test_x", records)
    assert profile.total_runs == 1
    assert profile.confidence == pytest.approx(1 / MIN_RUNS, abs=1e-6)
    assert profile.is_flaky is False


def test_fifty_real_runs_with_one_failure_is_flaky(detector: FlakinessDetector) -> None:
    # Positive control: the same 1-in-50 failure over fifty EXECUTED runs sits
    # exactly on FLAKY_MIN_RATE and must be flagged, at low severity.
    records = _records(passed=49, failed=1)
    profile = detector.compute_profile("test_x", records)
    assert profile.total_runs == 50
    assert profile.is_flaky is True
    assert profile.severity == "low"


def test_get_profiles_ignores_tests_whose_records_are_all_skips(
    store: HistoryStore, detector: FlakinessDetector
) -> None:
    for _ in range(20):
        store.record(_rec("test_always_skipped", "skipped"))
    assert all(p.test_id != "test_always_skipped" for p in detector.get_profiles())


def test_get_profiles_accepts_preloaded_groups(
    store: HistoryStore, detector: FlakinessDetector
) -> None:
    # pytest_sessionfinish parsed the whole history file twice per run; it can
    # hand the groups it already loaded straight to the detector.
    groups = {"test_x": _records(passed=8, failed=2)}
    profiles = detector.get_profiles(groups=groups)
    assert [p.test_id for p in profiles] == ["test_x"]
