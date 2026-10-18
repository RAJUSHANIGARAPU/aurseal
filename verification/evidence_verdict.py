"""What a coverage test must tell its reader: the facts it established, as observed values.

A detector says whether a known defect still reproduces. A coverage test
exists to prove a behaviour is present and correct, and its pass/fail cannot
tell apart the outcomes that matter:

=============  ==============================================================
proved         the subject was exercised and the oracle held: evidence
recorded       a figure was observed and written down, nothing asserted
held_empty  a check held vacuously because its data was absent
abstained      a precondition could not be arranged: no verdict at all
=============  ==============================================================

All four exit 0, and only ``proved`` is release evidence::

    verdict = EvidenceVerdict("REQ-12 renewal date")
    if not invoice.lines:
        verdict.no_verdict("no invoice with lines in this environment")
    assert invoice.total == sum(line.amount for line in invoice.lines)
    verdict.proved(f"total {invoice.total} = sum of {len(invoice.lines)} lines on {invoice.id}")

An abstention is legitimate only for a precondition the test cannot arrange
(an empty shared pool, an environment that lacks the feature). Anything the
test arranges on its own must be asserted, not skipped: skipping it would let a
broken harness pass as a green build.

``no_verdict`` skips, or fails when ``COVERAGE_VERDICT_REQUIRED`` is truthy:
a job that exists to produce a verdict turns "could not tell" into red.
"""

from __future__ import annotations

import logging
import os
from typing import NoReturn

import pytest

from verification.verdicts import CoverageOutcome, Kind, record_verdict

logger = logging.getLogger(__name__)

MARKER = "[coverage-verdict]"
REQUIRED_ENV = "COVERAGE_VERDICT_REQUIRED"
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def verdict_required() -> bool:
    return os.environ.get(REQUIRED_ENV, "").strip().lower() in _TRUTHY


class EvidenceVerdict:
    """One reporter per test; every call logs one line and appends one verdict record."""

    def __init__(self, subject: str, *, ticket: str | None = None) -> None:
        if not subject or not subject.strip():
            raise ValueError("a coverage verdict needs a subject a reader can grep for")
        self.subject = subject
        self.ticket = ticket

    def proved(self, evidence: str) -> None:
        """The subject was exercised and the oracle held.

        ``evidence`` names the observed values ("total 12.40 = 3 lines on INV-7"),
        never just "OK": this one line should be enough to judge the run.
        """
        if not evidence or not evidence.strip():
            raise ValueError(f"{self.subject}: proved() needs the observed values as evidence")
        logger.info("%s %s PROVED: %s", MARKER, self.subject, evidence)
        self._record(CoverageOutcome.PROVED, evidence)

    def recorded(self, figure: str) -> None:
        """Log a measured value without making any claim about it."""
        logger.warning("%s %s RECORDED, NOT ASSERTED: %s", MARKER, self.subject, figure)
        self._record(CoverageOutcome.RECORDED, figure)

    def held_empty(self, what_was_not_checked: str) -> None:
        """A check held vacuously. Does not stop the test; states the coverage lost."""
        logger.warning("%s %s HELD EMPTY: %s", MARKER, self.subject, what_was_not_checked)
        self._record(CoverageOutcome.HELD_EMPTY, what_was_not_checked)

    def no_verdict(self, reason: str) -> NoReturn:
        """Record an abstention, then skip (or fail when a verdict is required)."""
        message = f"{self.subject}: NO VERDICT, nothing was verified on this run: {reason}"
        logger.warning("%s %s", MARKER, message)
        self._record(CoverageOutcome.ABSTAINED, reason)
        if verdict_required():
            pytest.fail(f"{message} ({REQUIRED_ENV} is set, so an abstention is a failure)")
        pytest.skip(message)

    def _record(self, outcome: CoverageOutcome, detail: str) -> None:
        record_verdict(
            Kind.COVERAGE, outcome, ticket=self.ticket, subject=self.subject, detail=detail
        )
