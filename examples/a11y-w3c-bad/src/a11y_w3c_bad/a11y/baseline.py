"""Known violations: filed, excused from the gate, and expected to keep reproducing.

``a11y-baseline.json`` lists what is already filed, one entry per element and rule, each
with the issue that tracks it. The verdict on a scan:

- **new**: a serious or critical finding the baseline does not list. The gate fails.
- **known**: a finding the baseline lists. Reported, not failing.
- **cleared**: a baseline entry the scan no longer finds. It was fixed (or the page changed),
  so the entry must be removed: the file only ever shrinks.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .scanner import Finding, ScanResult

BASELINE = Path(__file__).resolve().parents[3] / "a11y-baseline.json"


@dataclass(frozen=True)
class KnownViolation:
    page: str
    rule: str
    target: str
    issue: str
    note: str = ""

    @property
    def key(self) -> tuple[str, str]:
        return (self.rule, self.target)


@dataclass(frozen=True)
class Verdict:
    new: tuple[Finding, ...]
    known: tuple[Finding, ...]
    cleared: tuple[KnownViolation, ...]


def load_baseline(path: Path = BASELINE) -> tuple[KnownViolation, ...]:
    """Every entry, checked: an excuse without an issue to track it is not accepted."""
    entries = json.loads(path.read_text(encoding="utf-8"))["violations"]
    known = tuple(KnownViolation(**entry) for entry in entries)
    for entry in known:
        if not entry.issue.strip():
            raise ValueError(f"{path.name}: {entry.page} {entry.rule} has no issue")
    keys = [(entry.page, *entry.key) for entry in known]
    if len(keys) != len(set(keys)):
        raise ValueError(f"{path.name}: duplicate entries")
    return known


def judge(page: str, result: ScanResult, baseline: tuple[KnownViolation, ...]) -> Verdict:
    """Split one page's scan into new, known and cleared, against the entries for ``page``."""
    entries = [entry for entry in baseline if entry.page == page]
    excused = {entry.key for entry in entries}
    found = {finding.key for finding in result.findings}
    return Verdict(
        new=tuple(f for f in result.blocking if f.key not in excused),
        known=tuple(f for f in result.findings if f.key in excused),
        cleared=tuple(entry for entry in entries if entry.key not in found),
    )
