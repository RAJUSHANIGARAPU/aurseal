"""Classify how a schedule's rows were registered with a job scheduler.

A billing schedule has rows (invoices, transactions) that a scheduler turns
into jobs. Two registration rules are common:

* **one at a time** — only the first pending row is registered; the next
  one is registered when it runs. Exactly one row is ``scheduled``, the rest
  wait as ``pending``.
* **all registered** — every unbooked row is registered at once; none waits.

:func:`classify_registration` reads which rule a schedule shows, so a test can
tell which build produced it. :func:`one_at_a_time_violations` lists breaches
of the one-at-a-time rule; :func:`silently_stalled` catches the failure that
raises nothing: a row was booked, rows still wait, and none is registered, so
the chain stopped. :func:`expected_run_time` gives when the registered job
should fire: the period start, or the registration moment plus the
scheduler's minimum delay when that is later.

Statuses are configurable through :class:`StatusVocabulary`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

DEFAULT_MIN_DELAY = timedelta(seconds=5)


class Rule(str, Enum):
    SINGLE_SCHEDULED = "one-at-a-time"
    """Exactly one scheduled, at least one pending."""
    EVERY_ROW_SCHEDULED = "all-registered"
    """Two or more scheduled, none pending."""
    NOTHING_SCHEDULED = "none-registered"
    """Unbooked rows exist but none is scheduled."""
    MIXED = "mixed"
    """Two or more scheduled next to pending ones: neither rule produces this."""
    UNDETERMINED = "undetermined"
    """Fewer than two unbooked rows: both rules look the same."""


@dataclass(frozen=True)
class StatusVocabulary:
    scheduled: frozenset[str] = frozenset({"scheduled"})
    pending: frozenset[str] = frozenset({"pending"})
    booked: frozenset[str] = frozenset({"booked", "paid"})


DEFAULT_VOCABULARY = StatusVocabulary()


@dataclass(frozen=True)
class ScheduleRow:
    id: str
    code: str
    status: str
    start: datetime | None
    job_id: str | None

    def __str__(self) -> str:
        when = self.start.date().isoformat() if self.start else "?"
        job = f"[job {self.job_id}]" if self.job_id else ""
        return f"{self.code}@{when}={self.status}{job}"


def parse_start(text: Any) -> datetime | None:
    """An offset-aware ISO date-time, or ``None`` when absent, unreadable or naive."""
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        value = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return value if value.tzinfo is not None else None


def parse_rows(
    raw: Iterable[Mapping[str, Any]] | None,
    *,
    id_key: str = "id",
    code_key: str = "code",
    status_key: str = "status",
    start_key: str = "start",
    job_key: str = "job_id",
) -> list[ScheduleRow]:
    """Rows from JSON objects; a missing list gives an empty one, a blank job id is ``None``."""
    rows = []
    for item in raw or ():
        job = item.get(job_key)
        job = str(job).strip() if job not in (None, "", "null") else None
        rows.append(
            ScheduleRow(
                id=str(item.get(id_key, "")),
                code=str(item.get(code_key, "")),
                status=str(item.get(status_key, "")).lower(),
                start=parse_start(item.get(start_key)),
                job_id=job or None,
            )
        )
    return rows


def _split(rows: list[ScheduleRow], vocab: StatusVocabulary):
    scheduled = [r for r in rows if r.status in vocab.scheduled]
    pending = [r for r in rows if r.status in vocab.pending]
    return scheduled, pending


def classify_registration(
    rows: Iterable[ScheduleRow], vocab: StatusVocabulary = DEFAULT_VOCABULARY
) -> Rule:
    scheduled, pending = _split(list(rows), vocab)
    if len(scheduled) + len(pending) < 2:
        return Rule.UNDETERMINED
    if len(scheduled) == 1:
        return Rule.SINGLE_SCHEDULED
    if not scheduled:
        return Rule.NOTHING_SCHEDULED
    return Rule.MIXED if pending else Rule.EVERY_ROW_SCHEDULED


def first_pending(
    rows: Iterable[ScheduleRow], vocab: StatusVocabulary = DEFAULT_VOCABULARY
) -> ScheduleRow | None:
    scheduled, pending = _split(list(rows), vocab)
    dated = [r for r in scheduled + pending if r.start is not None]
    return min(dated, key=lambda r: r.start) if dated else None


def one_at_a_time_violations(
    rows: Iterable[ScheduleRow], vocab: StatusVocabulary = DEFAULT_VOCABULARY
) -> list[str]:
    """Breaches of the one-at-a-time rule; empty when it holds."""
    rows = list(rows)
    scheduled, _ = _split(rows, vocab)
    if len(scheduled) != 1:
        listing = ", ".join(map(str, rows))
        return [f"expected exactly one scheduled row, found {len(scheduled)}: {listing}"]
    only = scheduled[0]
    violations = []
    earliest = first_pending(rows, vocab)
    if (
        earliest is not None
        and earliest.id != only.id
        and only.start is not None
        and earliest.start < only.start
    ):
        violations.append(
            f"the scheduled row {only} is not the first pending one: {earliest} starts before it"
        )
    if only.start is None:
        violations.append(f"the scheduled row {only.id} has no readable start")
    if not only.job_id:
        violations.append(f"the scheduled row {only} carries no job id")
    return violations


def silently_stalled(
    rows: Iterable[ScheduleRow], vocab: StatusVocabulary = DEFAULT_VOCABULARY
) -> bool:
    """Something was booked, rows still wait, and none is registered."""
    rows = list(rows)
    scheduled, pending = _split(rows, vocab)
    return any(r.status in vocab.booked for r in rows) and bool(pending) and not scheduled


def expected_run_time(
    period_start: datetime, registered_at: datetime, min_delay: timedelta = DEFAULT_MIN_DELAY
) -> datetime:
    """The period start, or ``registered_at + min_delay`` when that is later. Both must be aware."""
    if period_start.tzinfo is None or registered_at.tzinfo is None:
        raise ValueError("expected_run_time needs timezone-aware datetimes")
    earliest_allowed = registered_at + min_delay
    return max(period_start, earliest_allowed)
