from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

# A test must appear in at least this many runs before we make a flakiness judgement.
MIN_RUNS = 5
# Tests failing more than this rate are broken, not flaky — they need a fix, not monitoring.
ALWAYS_FAIL_THRESHOLD = 0.95
# Below this rate the failures are statistical noise.
FLAKY_MIN_RATE = 0.02


class FlakPattern(str, Enum):
    TIMING = "timing"  # timeout, wait condition, animation delay
    ORDER_DEPENDENT = "order_dependent"  # passes alone, fails with other tests
    RESOURCE_CONTENTION = "resource_contention"  # parallel worker competition
    DATA_POLLUTION = "data_pollution"  # shared test data mutated by prior test
    ENVIRONMENT = "environment"  # network, DNS, external service
    UNKNOWN = "unknown"


@dataclass
class FlakRecord:
    """One recorded test execution outcome."""

    test_id: str  # pytest node id: "ui/tests/test_search.py::test_search_train"
    run_id: str  # timestamp string: "20260425T143022Z"
    # "passed" | "failed" | "skipped" | "rerun".
    #
    # "rerun" is pytest-rerunfailures' own outcome for an attempt that failed
    # and will be retried, and it is a first-class value here rather than a
    # variant of "failed": the collapse in detector.py needs to tell a retried
    # attempt apart from a final verdict, and one failed attempt plus one
    # passing retry is one flaky *test*, not two runs.
    outcome: str
    duration_s: float
    error: str  # empty when passed or skipped; the attempt's failure text on "rerun"
    timestamp: str  # ISO-8601
    worker: str  # "main" for sequential, "gw0" / "gw1" for xdist
    environment: str  # "qa" | "staging" | "prod"

    def to_dict(self) -> dict:
        return {
            "test_id": self.test_id,
            "run_id": self.run_id,
            "outcome": self.outcome,
            "duration_s": self.duration_s,
            "error": self.error,
            "timestamp": self.timestamp,
            "worker": self.worker,
            "environment": self.environment,
        }

    @classmethod
    def from_dict(cls, data: dict) -> FlakRecord:
        return cls(
            test_id=data.get("test_id", ""),
            run_id=data.get("run_id", ""),
            outcome=data.get("outcome", "unknown"),
            duration_s=float(data.get("duration_s", 0.0)),
            error=data.get("error", ""),
            timestamp=data.get("timestamp", ""),
            worker=data.get("worker", "main"),
            environment=data.get("environment", "qa"),
        )


@dataclass
class FlakinessProfile:
    """Computed flakiness statistics for one test, derived from its run history."""

    test_id: str
    total_runs: int  # executions with a verdict — skips and unresolved retries excluded
    failure_count: int  # executions whose FINAL attempt failed
    # (failure_count + flaky_pass_count) / total_runs — "how often did this test
    # not pass cleanly". Not failure_count/total_runs any more: a run that failed
    # and then passed on retry is a misbehaviour with a failure_count of zero,
    # and reporting it at rate 0.0 filed the worst offenders as severity "low".
    flakiness_rate: float
    confidence: float  # min(total_runs / MIN_RUNS, 1.0)
    is_flaky: bool
    most_common_error: str
    avg_duration_s: float
    last_failure_ts: str  # ISO-8601 of most recent failed attempt, empty if none
    max_consecutive_failures: int  # longest streak of executions that ended failed
    # Executions that failed at least once and then passed on a retry. Direct,
    # single-observation proof of flakiness — no rate window needed.
    flaky_pass_count: int = 0

    @property
    def severity(self) -> str:
        if self.flakiness_rate > 0.20:
            return "high"
        if self.flakiness_rate > 0.05:
            return "medium"
        return "low"


@dataclass
class RemediationResult:
    """Targeted fix recommendation for a flaky test."""

    test_id: str
    pattern: FlakPattern
    strategy: str  # "add_explicit_wait" | "isolate_test" | "fix_parallelism" | "suggest_only"
    suggestion: str  # human-readable, actionable recommendation
    auto_applied: bool
    patched_file: Path | None = field(default=None)
