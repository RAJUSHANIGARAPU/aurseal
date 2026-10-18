"""Shared helper: attempts of one test -> one :class:`TestRecord`."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from aurseal.model.identity import (
    explicit_id,
    fingerprint,
    normalise_name,
    normalise_path,
    normalise_suite,
)
from aurseal.model.v1 import Attempt, Status, TestRecord


class ReportError(ValueError):
    """The report cannot be imported (malformed, or a test it cannot identify)."""


@dataclass
class PendingTest:
    """One test's identity and the attempts seen so far."""

    framework: str
    path: str
    suite: Sequence[str] | str
    title: str
    variant: str | None = None
    configuration: str | None = None
    tags: tuple[str, ...] = ()
    case_id: str | None = None
    attempts: list[Attempt] = field(default_factory=list)

    def add(
        self,
        status: Status,
        duration_ms: int | None = None,
        message: str | None = None,
        reason: str | None = None,
    ) -> None:
        self.attempts.append(
            Attempt(
                attempt_no=len(self.attempts),
                status=status,
                duration_ms=duration_ms,
                message=message,
                reason=reason,
            )
        )

    def record(self) -> TestRecord:
        if not self.attempts:
            raise ReportError(f"test {self.title!r} has no result")
        last = self.attempts[-1]
        status = last.status
        flaky = status is Status.PASSED and any(a.status.is_failure for a in self.attempts)
        durations = [a.duration_ms for a in self.attempts if a.duration_ms is not None]
        return TestRecord(
            framework=self.framework,
            path=normalise_path(self.path),
            suite=normalise_suite(self.suite),
            name=normalise_name(self.title),
            variant=self.variant,
            configuration=self.configuration,
            explicit_id=self.case_id or explicit_id(self.title, self.tags),
            fingerprint=fingerprint(self.framework, self.path, self.suite, self.title),
            status=status,
            flaky_in_run=flaky,
            attempts=tuple(self.attempts),
            duration_ms=sum(durations) if durations else None,
            message=last.message,
            reason=last.reason,
            tags=self.tags,
        )


def seconds_to_ms(value: str | float | None) -> int | None:
    if value is None or value == "":
        return None
    try:
        return max(0, round(float(value) * 1000))
    except ValueError:
        return None
