"""Accessibility scanning: axe-core in the page, typed findings out, a shrink-only baseline."""

from .baseline import KnownViolation, Verdict, judge, load_baseline
from .scanner import BLOCKING_IMPACTS, Finding, Scanner, ScanResult

__all__ = [
    "BLOCKING_IMPACTS",
    "Finding",
    "KnownViolation",
    "ScanResult",
    "Scanner",
    "Verdict",
    "judge",
    "load_baseline",
]
