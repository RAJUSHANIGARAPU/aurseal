"""Results history and trends, computed from the store's named queries.

One implementation for the dashboard, the REST handlers and the CLI:

* **Per case** — pass rate over the last N runs, where ``skipped``, ``not_run`` and
  ``xfailed`` are left out of the denominator but counted and shown (a case that only
  skips is *unmeasured*, never 100%); flips (pass <-> fail); the current streak and when
  the failure started; duration p50/p95 and drift (median of the last 5 against the
  median of the 20 before, flagged above ``RESULTS_DURATION_DRIFT``, default +50%);
  revision boundaries; passes that needed a retry.
* **Per suite / lane** — per-run totals, executed ratio, pass rate, wall time and the
  integrity verdict.
* **Run compare** — every test of a run against a baseline (default: the previous
  finished run on the same lane and branch) in one bucket: new failures, fixed, still
  failing (with a changed failure fingerprint called out), newly skipped, new tests,
  removed tests.
* **Unsound runs are flagged and excluded** from case windows by default
  (``include_unsound=False``): a run the integrity gate judged FAIL executed too little
  to count, and must not raise a pass rate.

A case's outcome in one run folds its parameter rows: any failure or error fails it,
else any pass passes it, else xfailed, else skipped, else not_run.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from itertools import pairwise
from typing import Any

from gate._env import env_float
from results.models import DEFAULT_PROJECT, RunRecord

DRIFT_ENV = "RESULTS_DURATION_DRIFT"
DEFAULT_DRIFT = 0.5
FAILING = frozenset({"failed", "error"})
PASSING = frozenset({"passed"})
NO_ANSWER = frozenset({"skipped", "not_run"})
_FOLD_ORDER = ("error", "failed", "passed", "xfailed", "skipped", "not_run")


def fold_outcomes(outcomes: Iterable[str]) -> str:
    """One outcome for a case in one run, from its parameter rows."""
    seen = set(outcomes)
    if "error" in seen or "failed" in seen:
        return "failed" if "failed" in seen else "error"
    for outcome in _FOLD_ORDER[2:]:
        if outcome in seen:
            return outcome
    return "not_run"


def percentile(values: Sequence[float], pct: float) -> float | None:
    """Nearest-rank percentile; None for no values."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100.0 * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def median(values: Sequence[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def rate_text(passed: int, executed: int, skipped: int = 0, not_run: int = 0) -> str:
    """A pass rate with its denominator; "unmeasured" when nothing executed."""
    extra = []
    if skipped:
        extra.append(f"{skipped} skipped")
    if not_run:
        extra.append(f"{not_run} not run")
    tail = f", {', '.join(extra)}" if extra else ""
    if executed == 0:
        return f"unmeasured (0 executed{tail})"
    return f"{100.0 * passed / executed:.0f}% ({passed}/{executed} executed{tail})"


# ---------------------------------------------------------------------- per case


@dataclass(frozen=True)
class HistoryPoint:
    run_id: str
    started_at: str | None
    outcome: str
    params: int
    duration_ms: int | None
    revision_id: str | None
    failure_fingerprint: str | None
    message: str | None
    integrity_verdict: str | None
    git_sha: str | None
    passed_after_retry: bool = False

    @property
    def unsound(self) -> bool:
        return self.integrity_verdict == "FAIL"


def case_history(
    store: Any, case_id: str, *, limit: int = 50, project_id: str = DEFAULT_PROJECT
) -> list[HistoryPoint]:
    """The case's outcome in each of its last ``limit`` runs, oldest first."""
    by_run: dict[str, list[dict[str, Any]]] = {}
    for row in store.query("case_history", case_id=case_id, project_id=project_id):
        by_run.setdefault(row["run_id"], []).append(row)
    points: list[HistoryPoint] = []
    for run_id, rows in by_run.items():
        outcome = fold_outcomes(r["outcome"] for r in rows)
        durations = [r["duration_ms"] for r in rows if r["duration_ms"] is not None]
        failing = [r for r in rows if r["outcome"] in FAILING]
        first = failing[0] if failing else rows[0]
        points.append(
            HistoryPoint(
                run_id=run_id,
                started_at=rows[0]["run_started_at"],
                outcome=outcome,
                params=len(rows),
                duration_ms=sum(durations) if durations else None,
                revision_id=rows[0]["revision_id"],
                failure_fingerprint=first["failure_fingerprint"] if failing else None,
                message=first["message"] if failing else None,
                integrity_verdict=rows[0]["integrity_verdict"],
                git_sha=rows[0]["git_sha"],
                passed_after_retry=outcome == "passed" and any(r["failed_attempts"] for r in rows),
            )
        )
    return points[-limit:] if limit else points


@dataclass(frozen=True)
class CaseTrend:
    case_id: str
    window: int
    runs: int
    executed: int
    passed: int
    failed: int
    xfailed: int
    skipped: int
    not_run: int
    excluded_unsound: int
    pass_rate: float | None  # None: unmeasured
    flips: int
    streak_outcome: str | None  # "passed" | "failed" | None
    streak_length: int
    failing_since_run_id: str | None
    passed_after_retry: int
    duration_p50_ms: float | None
    duration_p95_ms: float | None
    duration_drift: float | None  # recent median / baseline median - 1
    drift_flagged: bool
    revision_boundaries: tuple[str, ...]  # run ids where the revision changed

    @property
    def unmeasured(self) -> bool:
        return self.executed == 0

    @property
    def rate_text(self) -> str:
        return rate_text(self.passed, self.executed, self.skipped, self.not_run)

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["revision_boundaries"] = list(self.revision_boundaries)
        data["unmeasured"] = self.unmeasured
        data["rate_text"] = self.rate_text
        return data


def _state(outcome: str) -> str | None:
    if outcome in FAILING:
        return "failed"
    if outcome in PASSING:
        return "passed"
    return None


def trend_from_points(
    case_id: str,
    points: Sequence[HistoryPoint],
    *,
    window: int = 30,
    include_unsound: bool = False,
    drift_threshold: float | None = None,
) -> CaseTrend:
    """The trend of an already-loaded history (oldest first)."""
    threshold = env_float(DRIFT_ENV, DEFAULT_DRIFT) if drift_threshold is None else drift_threshold
    excluded = 0
    usable: list[HistoryPoint] = []
    for p in points:
        if p.unsound and not include_unsound:
            excluded += 1
            continue
        usable.append(p)
    usable = usable[-window:] if window else usable
    counts = {k: 0 for k in ("passed", "failed", "error", "xfailed", "skipped", "not_run")}
    for p in usable:
        counts[p.outcome] = counts.get(p.outcome, 0) + 1
    failed = counts["failed"] + counts["error"]
    executed = counts["passed"] + failed
    states = [s for s in (_state(p.outcome) for p in usable) if s]
    flips = sum(1 for a, b in pairwise(states) if a != b)
    streak_outcome = states[-1] if states else None
    streak = 0
    for s in reversed(states):
        if s != streak_outcome:
            break
        streak += 1
    failing_since = None
    if streak_outcome == "failed":
        for p in reversed(usable):
            st = _state(p.outcome)
            if st == "passed":
                break
            if st == "failed":
                failing_since = p.run_id
    durations = [
        float(p.duration_ms) for p in usable if p.duration_ms is not None and _state(p.outcome)
    ]
    recent, baseline = durations[-5:], durations[-25:-5]
    drift = None
    if len(recent) >= 3 and len(baseline) >= 3:
        base = median(baseline)
        if base:
            drift = (median(recent) or 0.0) / base - 1.0
    boundaries = tuple(
        b.run_id
        for a, b in pairwise(usable)
        if a.revision_id and b.revision_id and a.revision_id != b.revision_id
    )
    return CaseTrend(
        case_id=case_id,
        window=window,
        runs=len(usable),
        executed=executed,
        passed=counts["passed"],
        failed=failed,
        xfailed=counts["xfailed"],
        skipped=counts["skipped"],
        not_run=counts["not_run"],
        excluded_unsound=excluded,
        pass_rate=(counts["passed"] / executed) if executed else None,
        flips=flips,
        streak_outcome=streak_outcome,
        streak_length=streak,
        failing_since_run_id=failing_since,
        passed_after_retry=sum(1 for p in usable if p.passed_after_retry),
        duration_p50_ms=percentile(durations, 50),
        duration_p95_ms=percentile(durations, 95),
        duration_drift=drift,
        drift_flagged=drift is not None and drift > threshold,
        revision_boundaries=boundaries,
    )


def case_trend(
    store: Any,
    case_id: str,
    *,
    window: int = 30,
    include_unsound: bool = False,
    project_id: str = DEFAULT_PROJECT,
) -> CaseTrend:
    points = case_history(store, case_id, limit=0, project_id=project_id)
    return trend_from_points(case_id, points, window=window, include_unsound=include_unsound)


def failing_since(
    store: Any, case_id: str, *, project_id: str = DEFAULT_PROJECT
) -> RunRecord | None:
    """The first failing run since the case last passed; None when it is not failing."""
    trend = case_trend(store, case_id, window=0, project_id=project_id)
    if trend.failing_since_run_id is None:
        return None
    return store.run(trend.failing_since_run_id)


# ---------------------------------------------------------------------- per suite


def _seconds_between(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    try:
        a = datetime.fromisoformat(start.replace("Z", "+00:00"))
        b = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max((b - a).total_seconds(), 0.0)


@dataclass(frozen=True)
class RunPoint:
    run_id: str
    lane: str | None
    branch: str | None
    started_at: str | None
    status: str
    integrity_verdict: str | None
    selected: int
    executed: int
    passed: int
    failed: int
    xfailed: int
    skipped: int
    not_run: int
    passed_on_retry: int
    wall_time_s: float | None
    expected_count: int | None

    @property
    def pass_rate(self) -> float | None:
        return self.passed / self.executed if self.executed else None

    @property
    def executed_ratio(self) -> float | None:
        denominator = max(self.selected, self.expected_count or 0)
        return self.executed / denominator if denominator else None

    @property
    def unsound(self) -> bool:
        return self.integrity_verdict == "FAIL"

    @property
    def rate_text(self) -> str:
        return rate_text(self.passed, self.executed, self.skipped, self.not_run)

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data.update(
            pass_rate=self.pass_rate,
            executed_ratio=self.executed_ratio,
            unsound=self.unsound,
            rate_text=self.rate_text,
        )
        return data


def _run_point(row: dict[str, Any]) -> RunPoint:
    failed = int(row["failed"]) + int(row["error"])
    passed = int(row["passed"])
    return RunPoint(
        run_id=row["run_id"],
        lane=row["lane"],
        branch=row["git_branch"],
        started_at=row["started_at"],
        status=row["status"],
        integrity_verdict=row["integrity_verdict"],
        selected=int(row["selected"]),
        executed=passed + failed,
        passed=passed,
        failed=failed,
        xfailed=int(row["xfailed"]),
        skipped=int(row["skipped"]),
        not_run=int(row["not_run"]),
        passed_on_retry=int(row["passed_on_retry"]),
        wall_time_s=_seconds_between(row["started_at"], row["finished_at"]),
        expected_count=row["expected_count"],
    )


def suite_trend(
    store: Any,
    *,
    lane: str | None = None,
    branch: str | None = None,
    window: int = 30,
    project_id: str = DEFAULT_PROJECT,
) -> list[RunPoint]:
    """Per-run totals for the last ``window`` runs, oldest first."""
    rows = store.query("suite_runs", lane=lane, branch=branch, limit=window, project_id=project_id)
    return [_run_point(r) for r in reversed(rows)]


# ---------------------------------------------------------------------- compare


@dataclass
class RunComparison:
    run_id: str
    baseline_run_id: str | None
    new_failures: list[str] = field(default_factory=list)
    fixed: list[str] = field(default_factory=list)
    still_failing: list[str] = field(default_factory=list)
    fingerprint_changed: list[str] = field(default_factory=list)  # subset of still_failing
    newly_skipped: list[str] = field(default_factory=list)
    new_tests: list[str] = field(default_factory=list)
    removed_tests: list[str] = field(default_factory=list)
    messages: dict[str, str] = field(default_factory=dict)

    BUCKETS = (
        "new_failures",
        "fixed",
        "still_failing",
        "fingerprint_changed",
        "newly_skipped",
        "new_tests",
        "removed_tests",
    )

    def to_json(self) -> dict[str, Any]:
        data: dict[str, Any] = {"run_id": self.run_id, "baseline_run_id": self.baseline_run_id}
        for name in self.BUCKETS:
            data[name] = getattr(self, name)
        data["counts"] = {name: len(getattr(self, name)) for name in self.BUCKETS}
        return data

    def to_markdown(self) -> str:
        if self.baseline_run_id is None:
            return (
                f"### Run compare: {self.run_id}\n\nNo baseline: no earlier finished run on "
                "this lane and branch is stored, so there is nothing to compare against.\n"
            )
        lines = [f"### Run compare: {self.run_id} vs {self.baseline_run_id}", ""]
        titles = {
            "new_failures": "New failures",
            "fixed": "Fixed",
            "still_failing": "Still failing",
            "fingerprint_changed": "Still failing, for a different reason",
            "newly_skipped": "Newly skipped or not run",
            "new_tests": "New tests",
            "removed_tests": "Removed tests",
        }
        lines.append("| Bucket | Tests |")
        lines.append("|---|---|")
        for name in self.BUCKETS:
            lines.append(f"| {titles[name]} | {len(getattr(self, name))} |")
        lines.append("")
        for name in ("new_failures", "fingerprint_changed", "fixed", "newly_skipped"):
            items = getattr(self, name)
            if not items:
                continue
            lines.append(f"**{titles[name]}**")
            for nodeid in items[:50]:
                msg = self.messages.get(nodeid)
                lines.append(f"- `{nodeid}`" + (f" — {msg[:160]}" if msg else ""))
            if len(items) > 50:
                lines.append(f"- … and {len(items) - 50} more")
            lines.append("")
        return "\n".join(lines)


def baseline_for(store: Any, run_id: str) -> str | None:
    rows = store.query("baseline_run", run_id=run_id)
    return rows[0]["run_id"] if rows else None


def compare_runs(store: Any, run_id: str, baseline_run_id: str | None = None) -> RunComparison:
    """Every test of ``run_id`` against the baseline run, sorted into buckets."""
    if not store.query("run_get", run_id=run_id):
        from results.store import StoreError

        raise StoreError(f"unknown run {run_id!r}")
    baseline = baseline_run_id or baseline_for(store, run_id)
    result = RunComparison(run_id=run_id, baseline_run_id=baseline)
    current = {r["nodeid"]: r for r in store.query("run_final_results", run_id=run_id)}
    if baseline is None:
        return result
    before = {r["nodeid"]: r for r in store.query("run_final_results", run_id=baseline)}
    for nodeid in sorted(current):
        now = current[nodeid]
        if now["message"]:
            result.messages[nodeid] = now["message"]
        prev = before.get(nodeid)
        if prev is None:
            result.new_tests.append(nodeid)
            continue
        now_fail, prev_fail = now["outcome"] in FAILING, prev["outcome"] in FAILING
        if now_fail and prev_fail:
            result.still_failing.append(nodeid)
            if (now["failure_fingerprint"] or "") != (prev["failure_fingerprint"] or ""):
                result.fingerprint_changed.append(nodeid)
        elif now_fail:
            result.new_failures.append(nodeid)
        elif prev_fail and now["outcome"] in PASSING:
            result.fixed.append(nodeid)
        elif now["outcome"] in NO_ANSWER and prev["outcome"] not in NO_ANSWER:
            result.newly_skipped.append(nodeid)
    result.removed_tests = sorted(set(before) - set(current))
    return result


# ---------------------------------------------------------------------- top lists


def _by_case(rows: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row["case_id"] or row["nodeid"], []).append(row)
    return grouped


def slowest(
    store: Any,
    *,
    window: int = 30,
    top: int = 20,
    lane: str | None = None,
    project_id: str = DEFAULT_PROJECT,
) -> list[dict[str, Any]]:
    """Cases by median duration over the last ``window`` sound runs."""
    out = []
    rows = store.query("window_results", window=window, lane=lane, project_id=project_id)
    for key, items in _by_case(rows).items():
        durations = [float(r["duration_ms"]) for r in items if r["duration_ms"] is not None]
        if not durations:
            continue
        out.append(
            {
                "case_id": items[0]["case_id"],
                "key": key,
                "title": items[0]["title"] or items[0]["case_key"] or items[0]["nodeid"],
                "p50_ms": percentile(durations, 50),
                "p95_ms": percentile(durations, 95),
                "samples": len(durations),
            }
        )
    out.sort(key=lambda d: (-(d["p50_ms"] or 0), d["key"]))
    return out[:top]


def unstable(
    store: Any,
    *,
    window: int = 30,
    min_flips: int = 2,
    lane: str | None = None,
    project_id: str = DEFAULT_PROJECT,
) -> list[dict[str, Any]]:
    """Cases that flip pass<->fail at least ``min_flips`` times, or passed only on a retry."""
    out = []
    rows = store.query("window_results", window=window, lane=lane, project_id=project_id)
    for key, items in _by_case(rows).items():
        per_run: dict[str, list[dict[str, Any]]] = {}
        for r in items:
            per_run.setdefault(r["run_id"], []).append(r)
        outcomes = [fold_outcomes(x["outcome"] for x in rs) for rs in per_run.values()]
        states = [s for s in (_state(o) for o in outcomes) if s]
        flips = sum(1 for a, b in pairwise(states) if a != b)
        retried = sum(
            1
            for rs in per_run.values()
            if fold_outcomes(x["outcome"] for x in rs) == "passed"
            and any(x["failed_attempts"] for x in rs)
        )
        if flips >= min_flips or retried:
            executed = len(states)
            passed = states.count("passed")
            out.append(
                {
                    "case_id": items[0]["case_id"],
                    "key": key,
                    "title": items[0]["title"] or items[0]["case_key"] or items[0]["nodeid"],
                    "flips": flips,
                    "passed_after_retry": retried,
                    "executed": executed,
                    "passed": passed,
                    "rate_text": rate_text(passed, executed),
                }
            )
    out.sort(key=lambda d: (-(d["flips"] + d["passed_after_retry"]), d["key"]))
    return out


__all__ = [
    "CaseTrend",
    "HistoryPoint",
    "RunComparison",
    "RunPoint",
    "baseline_for",
    "case_history",
    "case_trend",
    "compare_runs",
    "failing_since",
    "fold_outcomes",
    "percentile",
    "rate_text",
    "slowest",
    "suite_trend",
    "trend_from_points",
    "unstable",
]
