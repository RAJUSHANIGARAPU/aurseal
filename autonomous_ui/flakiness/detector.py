"""Computes flakiness profiles from test run history.

A test is classified as flaky when:
  - it has been run at least MIN_RUNS times (enough data for confidence)
  - its failure rate is between FLAKY_MIN_RATE and ALWAYS_FAIL_THRESHOLD
    (below the lower bound = noise; above the upper bound = broken, not flaky)
"""

from __future__ import annotations

from collections import Counter

from autonomous_ui.flakiness.history_store import HistoryStore
from autonomous_ui.flakiness.models import (
    ALWAYS_FAIL_THRESHOLD,
    FLAKY_MIN_RATE,
    MIN_RUNS,
    FlakinessProfile,
    FlakRecord,
)


class FlakinessDetector:
    """Derives FlakinessProfile objects from stored test run history."""

    def __init__(self, store: HistoryStore, min_runs: int = MIN_RUNS) -> None:
        self._store = store
        self._min_runs = min_runs

    def compute_profile(self, test_id: str, records: list[FlakRecord]) -> FlakinessProfile:
        """Build a FlakinessProfile from a pre-loaded list of records for one test."""
        total = len(records)
        failures = [r for r in records if r.outcome == "failed"]
        failure_count = len(failures)
        rate = failure_count / total if total > 0 else 0.0
        confidence = min(total / self._min_runs, 1.0)

        is_flaky = (
            total >= self._min_runs
            and 0 < failure_count
            and FLAKY_MIN_RATE <= rate < ALWAYS_FAIL_THRESHOLD
        )

        # Most common error among failed runs
        errors = [r.error for r in failures if r.error]
        most_common = Counter(errors).most_common(1)[0][0] if errors else ""

        avg_duration = sum(r.duration_s for r in records) / total if total else 0.0

        last_failure_ts = max((r.timestamp for r in failures), default="")

        max_consecutive = self._max_consecutive_failures(records)

        return FlakinessProfile(
            test_id=test_id,
            total_runs=total,
            failure_count=failure_count,
            flakiness_rate=rate,
            confidence=confidence,
            is_flaky=is_flaky,
            most_common_error=most_common,
            avg_duration_s=avg_duration,
            last_failure_ts=last_failure_ts,
            max_consecutive_failures=max_consecutive,
        )

    def get_profiles(self) -> list[FlakinessProfile]:
        """Compute profiles for all tests that have at least min_runs recorded."""
        groups = self._store.grouped_by_test()
        profiles = []
        for test_id, records in groups.items():
            if len(records) < self._min_runs:
                continue
            profiles.append(self.compute_profile(test_id, records))
        return sorted(profiles, key=lambda p: p.flakiness_rate, reverse=True)

    def get_flaky_tests(self) -> list[FlakinessProfile]:
        """Return only tests classified as flaky, sorted by rate descending."""
        return [p for p in self.get_profiles() if p.is_flaky]

    @staticmethod
    def _max_consecutive_failures(records: list[FlakRecord]) -> int:
        current = max_streak = 0
        for rec in records:
            if rec.outcome == "failed":
                current += 1
                max_streak = max(max_streak, current)
            else:
                current = 0
        return max_streak
