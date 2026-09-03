"""Computes flakiness profiles from test run history.

A test is classified as flaky when it has at least MIN_RUNS *executions* with a
verdict and either:
  - it failed at least once and then passed on a retry in the same invocation
    (direct proof — no statistics required), or
  - its rate of not-passing-cleanly is at or above FLAKY_MIN_RATE.
…and it is not failing outright: an execution-level hard failure rate at or
above ALWAYS_FAIL_THRESHOLD means broken, which needs a fix, not monitoring.

Records are not runs
--------------------
One invocation of a test can produce several records. pytest-rerunfailures
emits an outcome of "rerun" for every attempt it is going to retry, then one
final "passed" or "failed". ``autonomous_ui/orchestrator.py`` sets
``--reruns 2`` for every test the healer flags, so this is the normal shape for
exactly the tests this subsystem cares about most.

So the records for a test are first collapsed into executions: a run of
consecutive "rerun" records plus the terminal record that closes them is ONE
execution. One failing attempt plus one passing retry is one flaky *test*, not
two runs — counting the attempts separately would report a test that fails
every single time on the first try as "50% flaky", and would double every
denominator the moment reruns are switched on.

Three things follow from that, and each of them was a measured wrong verdict
before the collapse existed:

  * A ``[rerun, passed]`` execution is a **flaky pass**. It is the strongest
    single observation of flakiness there is, so it sets ``is_flaky`` on its
    own. It is deliberately NOT counted in ``failure_count`` (the test did
    ultimately pass) but it does count in ``flakiness_rate``, or a test that
    flakes on every invocation would score 0.0 and read as "low".
  * A ``[rerun, rerun, failed]`` execution is ONE failure, not one failure and
    two skips. Recorded as attempts it gave rate 0.33, and after just two
    invocations total=6 >= MIN_RUNS with confidence 1.0 — a permanently broken
    test reported as flaky and handed "add an explicit wait".
  * Trailing "rerun" records with no terminal record mean the session died
    mid-retry. There is no verdict, so there is no execution — inventing a
    failure there would blame the test for an interrupted run.

Skips are not runs either
-------------------------
A skipped execution is not evidence about the test, so it stays out of
``total_runs``, out of ``failure_count`` and out of ``confidence``. Counting it
was enough to duck the always-fail threshold entirely: 9 failed + 1 skipped
scored rate 0.90 and came back is_flaky True, severity high, confidence 1.0 —
a broken test filed as flaky. In the other direction 49 skipped + 1 failed
scored rate 0.020 with confidence 1.0: a flaky verdict from a single execution.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from autonomous_ui.flakiness.history_store import HistoryStore
from autonomous_ui.flakiness.models import (
    ALWAYS_FAIL_THRESHOLD,
    FLAKY_MIN_RATE,
    MIN_RUNS,
    FlakinessProfile,
    FlakRecord,
)

# Outcomes that mean "this attempt did not pass". Both carry error text worth
# keeping: on a flaky pass the retried attempt is the ONLY place the failure
# message exists, and PatternAnalyzer classifies from exactly that text.
_FAILED_ATTEMPT = ("failed", "rerun")


@dataclass(frozen=True)
class _Execution:
    """One invocation of a test, with every retry attempt folded in."""

    final: str  # "passed" | "failed" | "skipped"
    retried: bool  # at least one attempt failed before the final one
    duration_s: float  # summed across attempts

    @property
    def counts_as_run(self) -> bool:
        return self.final in ("passed", "failed")

    @property
    def is_hard_failure(self) -> bool:
        return self.final == "failed"

    @property
    def is_flaky_pass(self) -> bool:
        return self.final == "passed" and self.retried


class FlakinessDetector:
    """Derives FlakinessProfile objects from stored test run history."""

    def __init__(self, store: HistoryStore, min_runs: int = MIN_RUNS) -> None:
        self._store = store
        self._min_runs = min_runs

    def compute_profile(self, test_id: str, records: list[FlakRecord]) -> FlakinessProfile:
        """Build a FlakinessProfile from a pre-loaded list of records for one test."""
        executions = self._collapse(records)
        runs = [e for e in executions if e.counts_as_run]

        total = len(runs)
        failure_count = sum(1 for e in runs if e.is_hard_failure)
        flaky_pass_count = sum(1 for e in runs if e.is_flaky_pass)

        rate = (failure_count + flaky_pass_count) / total if total else 0.0
        hard_failure_rate = failure_count / total if total else 0.0
        confidence = min(total / self._min_runs, 1.0)

        # The always-fail cut-off reads the HARD failure rate, never `rate`.
        # A test that flakes on every invocation has rate 1.0 with zero hard
        # failures, and comparing that against the threshold would dismiss the
        # most flaky test in the suite as "broken, not flaky".
        always_failing = hard_failure_rate >= ALWAYS_FAIL_THRESHOLD

        is_flaky = (
            total >= self._min_runs
            and not always_failing
            and (flaky_pass_count > 0 or (failure_count > 0 and rate >= FLAKY_MIN_RATE))
        )

        # Diagnostics come from the attempt records, not the executions: a
        # retried attempt is where the error text lives.
        failed_attempts = [r for r in records if r.outcome in _FAILED_ATTEMPT]
        errors = [r.error for r in failed_attempts if r.error]
        most_common = Counter(errors).most_common(1)[0][0] if errors else ""
        last_failure_ts = max((r.timestamp for r in failed_attempts), default="")

        avg_duration = sum(e.duration_s for e in runs) / total if total else 0.0

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
            max_consecutive_failures=self._max_consecutive_failures(executions),
            flaky_pass_count=flaky_pass_count,
        )

    def get_profiles(
        self, groups: dict[str, list[FlakRecord]] | None = None
    ) -> list[FlakinessProfile]:
        """Compute profiles for all tests with at least min_runs executions.

        ``groups`` lets a caller that has already read the history pass it in.
        ``pytest_sessionfinish`` parsed the whole file twice on every run — at
        5.4 MB / 18,486 records in the source repo, that is not free.
        """
        if groups is None:
            groups = self._store.grouped_by_test()
        profiles = [self.compute_profile(test_id, recs) for test_id, recs in groups.items()]
        # Filtered on executions, not raw records: a test with 50 skipped
        # records has no run history at all and must not produce a profile.
        profiles = [p for p in profiles if p.total_runs >= self._min_runs]
        return sorted(profiles, key=lambda p: p.flakiness_rate, reverse=True)

    def get_flaky_tests(self) -> list[FlakinessProfile]:
        """Return only tests classified as flaky, sorted by rate descending."""
        return [p for p in self.get_profiles() if p.is_flaky]

    @staticmethod
    def _collapse(records: list[FlakRecord]) -> list[_Execution]:
        """Fold retry attempts into the execution they belong to.

        Deliberately positional rather than grouped by ``run_id``: run_id is one
        pytest session, and a session legitimately executes a node id more than
        once (pytest-repeat, a re-collected parametrisation). Consecutive
        "rerun" records followed by a terminal record is the shape
        pytest-rerunfailures actually emits, and it needs no extra field.
        """
        executions: list[_Execution] = []
        pending_attempts = 0
        pending_duration = 0.0

        for rec in records:
            if rec.outcome == "rerun":
                pending_attempts += 1
                pending_duration += rec.duration_s
                continue
            executions.append(
                _Execution(
                    final=rec.outcome,
                    retried=pending_attempts > 0,
                    duration_s=pending_duration + rec.duration_s,
                )
            )
            pending_attempts = 0
            pending_duration = 0.0

        # Trailing reruns are dropped on purpose — see the module docstring.
        return executions

    @staticmethod
    def _max_consecutive_failures(executions: list[_Execution]) -> int:
        current = max_streak = 0
        for execution in executions:
            if execution.is_hard_failure:
                current += 1
                max_streak = max(max_streak, current)
            elif execution.counts_as_run:
                # A pass ends the streak even when it took a retry to get there.
                # A skip is not a verdict, so it neither extends nor breaks one.
                current = 0
        return max_streak
