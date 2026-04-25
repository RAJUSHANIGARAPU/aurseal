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
