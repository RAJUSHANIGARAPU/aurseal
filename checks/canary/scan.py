"""Scan targets for a run's canaries and reach one verdict per target.

* ``STILL_LEAKING``: a canary was found in a source of the target, in the window.
* ``CLEARED``: every source was searched completely and nothing was found.
* ``NOT_ASSESSED``: a source is missing, the result was truncated, the backend failed or
  timed out, or the target has no source.

A hit is proof even when the search was incomplete, so it is never downgraded.
``CLEARED`` only says the flows this run exercised did not log the canaries; a code path the
run never reached is not covered.

The report carries counts per source and kind. A matched line is dropped as soon as it has
been counted: it is never printed, stored or put in an error message.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from checks._outcome import CheckOutcome
from checks.canary.backends import (
    DEFAULT_ROW_LIMIT,
    DEFAULT_TIMEOUT,
    BackendError,
    LogBackend,
    Row,
)
from checks.canary.canaries import RunArtifact
from checks.canary.matching import Matcher, prefilter_pattern
from checks.canary.targets import Source, Target

KIND = "canary-log-scan"
DEFAULT_GRACE = timedelta(minutes=2)


class Verdict(str, Enum):
    STILL_LEAKING = "STILL_LEAKING"
    CLEARED = "CLEARED"
    NOT_ASSESSED = "NOT_ASSESSED"


@dataclass
class TargetResult:
    target: str
    verdict: Verdict
    reason: str
    hits: dict[str, dict[str, int]] = field(default_factory=dict)
    rows_scanned: int = 0
    truncated: bool = False
    missing: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "verdict": self.verdict.value,
            "reason": self.reason,
            "hits": self.hits,
            "rows_scanned": self.rows_scanned,
            "truncated": self.truncated,
            "missing_sources": self.missing,
        }


def _accepted(row: Row, sources: Sequence[Source]) -> bool:
    return any(s.source == row.source and s.accepts(row.text) for s in sources)


def scan_target(
    target: Target,
    artifact: RunArtifact,
    backend: LogBackend,
    *,
    limit: int = DEFAULT_ROW_LIMIT,
    timeout: float = DEFAULT_TIMEOUT,
    grace: timedelta = DEFAULT_GRACE,
    now: datetime | None = None,
) -> TargetResult:
    if not target.sources:
        return TargetResult(target.name, Verdict.NOT_ASSESSED, "the target lists no source")
    if not artifact.canaries:
        return TargetResult(target.name, Verdict.NOT_ASSESSED, "the run seeded no canary")
    ended = artifact.ended_at or now or datetime.now(timezone.utc)
    names = list(dict.fromkeys(s.source for s in target.sources))
    try:
        found = backend.search(
            names,
            prefilter_pattern(artifact.canaries),
            artifact.started_at,
            ended + grace,
            limit=limit,
            timeout=timeout,
        )
    except BackendError as exc:
        return TargetResult(target.name, Verdict.NOT_ASSESSED, f"backend: {exc}")

    matcher = Matcher(artifact.canaries)
    hits: dict[str, Counter[str]] = {}
    scanned = 0
    for row in found.rows:
        if not _accepted(row, target.sources):
            continue
        scanned += 1
        for kind in matcher.kinds_in(row.text):
            hits.setdefault(row.source, Counter())[kind] += 1
    counts = {src: dict(sorted(c.items())) for src, c in sorted(hits.items())}
    notes = []
    if found.truncated:
        notes.append(f"truncated at {limit} rows")
    if found.missing:
        notes.append(f"{len(found.missing)} source(s) missing")
    result = TargetResult(
        target.name,
        Verdict.CLEARED,
        "",
        hits=counts,
        rows_scanned=scanned,
        truncated=found.truncated,
        missing=sorted(found.missing),
    )
    if counts:
        total = sum(n for per in counts.values() for n in per.values())
        result.verdict = Verdict.STILL_LEAKING
        result.reason = f"{total} canary hit(s) in {len(counts)} source(s)" + (
            f" (search incomplete: {'; '.join(notes)})" if notes else ""
        )
    elif notes:
        result.verdict = Verdict.NOT_ASSESSED
        result.reason = "; ".join(notes) + ": an incomplete search cannot clear a target"
    else:
        result.reason = f"no canary in {len(names)} source(s) for the run's window"
    return result


def scan_targets(
    targets: Sequence[Target],
    artifact: RunArtifact,
    backend: LogBackend,
    **kwargs: Any,
) -> list[TargetResult]:
    return [scan_target(t, artifact, backend, **kwargs) for t in targets]


def to_outcome(results: Sequence[TargetResult]) -> CheckOutcome:
    """A leak is a measured failure; an unanswered target is unknown; else a pass.

    CLI exit codes follow ``checks._outcome``: 0 all cleared, 1 a leak, 2 incomplete.
    """
    by_verdict = Counter(r.verdict for r in results)
    metrics = {v.value.lower(): by_verdict.get(v, 0) for v in Verdict}
    findings = [r.to_json() for r in results if r.verdict is not Verdict.CLEARED]
    if by_verdict[Verdict.STILL_LEAKING]:
        return CheckOutcome.breached(
            KIND,
            f"{by_verdict[Verdict.STILL_LEAKING]} target(s) still leaking",
            metrics=metrics,
            findings=findings,
        )
    if by_verdict[Verdict.NOT_ASSESSED] or not results:
        return CheckOutcome.unknown(
            KIND,
            f"{by_verdict[Verdict.NOT_ASSESSED]} target(s) could not be assessed",
            metrics=metrics,
            findings=findings,
        )
    return CheckOutcome.ok(
        KIND, "every target cleared for the flows this run exercised", metrics=metrics
    )
