"""Named validation steps: every oracle assertion shows up in the report with its intent.

``with validating("refund is negative", expected="< 0"):`` wraps an assertion in
an Allure step that states what is being checked and what was expected, and
mirrors it in the log as ``VALIDATING`` / ``OK`` / ``FAILED``. :func:`skipped`
records a deliberate non-check as a skipped step, so an absent validation is
visible in the report instead of looking like a pass. :func:`note` attaches a
diagnostic (a description, a payload) to the current step.

Works without an Allure run (the step simply is not recorded).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

import allure
from allure_commons.types import AttachmentType

logger = logging.getLogger(__name__)


@contextmanager
def validating(intent: str, expected: object | None = None) -> Iterator[None]:
    title = intent if expected is None else f"{intent} (expected {expected})"
    logger.info("VALIDATING %s", title)
    with allure.step(title):
        try:
            yield
        except BaseException:
            logger.error("FAILED %s", title)
            raise
    logger.info("OK %s", title)


def skipped(intent: str, reason: str) -> None:
    """Record a check that was deliberately not run, and why."""
    logger.info("SKIPPED %s: %s", intent, reason)
    with allure.step(f"SKIPPED: {intent} — {reason}"):
        pass


def note(name: str, body: str) -> None:
    allure.attach(body, name=name, attachment_type=AttachmentType.TEXT)
