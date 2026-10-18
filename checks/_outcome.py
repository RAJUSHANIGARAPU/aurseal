"""One outcome object for every check engine, and one mapping onto plugin statuses.

The mapping is the honest-status contract the plugins already keep:

| Outcome | Plugin status |
|---|---|
| measured, every threshold met | ``pass`` |
| measured, a threshold breached | ``fail`` |
| tool missing, no target, unreachable, cut short, unparseable, nothing measured | ``unknown`` |
| the harness rejected its own command line | ``error`` |
| dry run, or a run that only took baselines | ``skip`` (scored neither way) |

``warn`` is never produced: it counts as passing, so a breached threshold reported as a
warning would be a pass by another name.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PASS = "pass"
FAIL = "fail"
UNKNOWN = "unknown"
ERROR = "error"
SKIP = "skip"


@dataclass
class CheckOutcome:
    """What one engine run established.

    ``measured`` is the load-bearing field: ``passed`` is only read when the engine
    actually measured something. ``passed=None`` with ``measured=False`` is the
    "could not reach a verdict" case.
    """

    kind: str
    measured: bool
    passed: bool | None
    reason: str
    metrics: dict[str, Any] = field(default_factory=dict)
    findings: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[Path] = field(default_factory=list)
    harness_error: bool = False
    dry_run: bool = False
    baseline_taken: bool = False

    @classmethod
    def ok(cls, kind: str, reason: str, **kw: Any) -> CheckOutcome:
        return cls(kind=kind, measured=True, passed=True, reason=reason, **kw)

    @classmethod
    def breached(cls, kind: str, reason: str, **kw: Any) -> CheckOutcome:
        return cls(kind=kind, measured=True, passed=False, reason=reason, **kw)

    @classmethod
    def unknown(cls, kind: str, reason: str, **kw: Any) -> CheckOutcome:
        return cls(kind=kind, measured=False, passed=None, reason=reason, **kw)

    @classmethod
    def error(cls, kind: str, reason: str, **kw: Any) -> CheckOutcome:
        return cls(kind=kind, measured=False, passed=None, reason=reason, harness_error=True, **kw)

    @classmethod
    def skipped(
        cls, kind: str, reason: str, *, baseline_taken: bool = False, **kw: Any
    ) -> CheckOutcome:
        """A dry run or a baseline-taking run: nothing was judged, on purpose."""
        return cls(
            kind=kind,
            measured=False,
            passed=None,
            reason=reason,
            dry_run=not baseline_taken,
            baseline_taken=baseline_taken,
            **kw,
        )

    @property
    def status(self) -> str:
        return to_plugin_status(self)

    def summary(self) -> dict[str, Any]:
        """A JSON-friendly finding for a plugin result or a CLI report."""
        out: dict[str, Any] = {"kind": self.kind, "status": self.status, "reason": self.reason}
        if self.metrics:
            out["metrics"] = self.metrics
        if self.findings:
            out["findings"] = self.findings
        if self.evidence:
            out["evidence"] = [str(p) for p in self.evidence]
        if self.dry_run:
            out["dry_run"] = True
        if self.baseline_taken:
            out["baseline_taken"] = True
        return out


def to_plugin_status(outcome: CheckOutcome) -> str:
    """The plugin status for an outcome. The only place this decision is made."""
    if outcome.harness_error:
        return ERROR
    if outcome.dry_run or outcome.baseline_taken:
        return SKIP
    if not outcome.measured or outcome.passed is None:
        return UNKNOWN
    return PASS if outcome.passed else FAIL


def exit_code(outcome: CheckOutcome) -> int:
    """CLI exit code: 0 only for a measured pass. A skip is 0 (nothing was asked)."""
    status = to_plugin_status(outcome)
    if status in (PASS, SKIP):
        return 0
    if status == FAIL:
        return 1
    return 2
