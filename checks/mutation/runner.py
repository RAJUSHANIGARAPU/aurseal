"""Run mutation testing for the configured packages and record the scores.

``MutationRunner`` is the seam for another tool; ``MutmutRunner`` is the one built.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from checks._outcome import CheckOutcome
from checks._sink import REPO_ROOT, MetricSink, metric_row
from checks.mutation import mutmut
from checks.mutation.config import MutationConfig, PackageScope
from checks.mutation.judge import KIND, judge, score

SCORE_TABLE = "metric_mutation_score"
SURVIVOR_TABLE = "metric_mutation_survivor"
WORK_ROOT = REPO_ROOT / "reports" / "mutation" / "work"
SURVIVOR_ROWS = 20


class MutationRunner(Protocol):
    def run(self, config: MutationConfig, scope: PackageScope) -> mutmut.MutmutRun: ...


class MutmutRunner:
    def __init__(self, repo: Path = REPO_ROOT, work_root: Path = WORK_ROOT) -> None:
        self.repo = repo
        self.work_root = work_root

    def run(self, config: MutationConfig, scope: PackageScope) -> mutmut.MutmutRun:
        return mutmut.run(config, scope, repo=self.repo, work_root=self.work_root)


def run_mutation(
    config: MutationConfig,
    packages: Sequence[str] | None = None,
    *,
    runner: MutationRunner | None = None,
    sink: MetricSink | None = None,
) -> CheckOutcome:
    try:
        scopes = config.select(list(packages) if packages else None)
    except ValueError as exc:
        return CheckOutcome.error(KIND, str(exc))
    runner = runner or MutmutRunner()
    counts: dict[str, mutmut.MutationCounts | None] = {}
    reasons: dict[str, str] = {}
    versions: dict[str, str | None] = {}
    for scope in scopes:
        try:
            result = runner.run(config, scope)
        except mutmut.MutmutUnavailable as exc:
            return CheckOutcome.unknown(KIND, str(exc))
        counts[scope.name] = result.counts if result.ran else None
        versions[scope.name] = result.tool_version
        if not result.ran:
            reasons[scope.name] = result.reason
    outcome = judge(counts, {s.name: s.threshold for s in scopes}, reasons=reasons)
    if sink is not None:
        _record(scopes, counts, versions, sink)
    return outcome


def _record(scopes, counts, versions, sink: MetricSink) -> None:
    scores, survivors = [], []
    for scope in scopes:
        c = counts.get(scope.name)
        if c is None:
            continue
        scores.append(
            metric_row(
                package=scope.name,
                killed=c.killed,
                survived=c.survived,
                timeout=c.timeout,
                no_tests=c.no_tests,
                score=score(c),
                threshold=scope.threshold,
                tool_version=versions.get(scope.name),
            )
        )
        for name in c.survivors[:SURVIVOR_ROWS]:
            module, _, _ = name.partition(".x")
            survivors.append(
                metric_row(package=scope.name, mutant_id=name, location=module, operator=None)
            )
    sink.write(SCORE_TABLE, scores)
    sink.write(SURVIVOR_TABLE, survivors)
