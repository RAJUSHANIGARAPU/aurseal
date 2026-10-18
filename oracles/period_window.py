"""Choose an action date strictly inside a booked period, clear of everything that would reject it.

A mid-period change (a cancellation, an amendment) needs a date that the
service's own validators accept: inside the last booked period, not covered by
an already-scheduled one, and not after the entity's validity end. Picking
"today + 20" and hoping is how a test ends up asserting on a validation error.

:func:`choose_mid_period_date` returns the date or a :class:`NoWindow` with the
reason, so the caller skips (or picks another entity) with a message that says
which rule ruled it out.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class Window:
    start: date
    end: date  # inclusive

    def covers(self, d: date) -> bool:
        return self.start <= d <= self.end


@dataclass(frozen=True)
class NoWindow:
    reason: str

    def __bool__(self) -> bool:
        return False


def last_booked_period(
    periods: Iterable[tuple[date, date, str]], booked_states: frozenset[str] = frozenset({"BOOKED"})
) -> Window | None:
    """The booked ``(start, end_inclusive, state)`` period that ends last, or None."""
    booked = [Window(s, e) for s, e, state in periods if state in booked_states]
    return max(booked, key=lambda w: w.end) if booked else None


def choose_mid_period_date(
    period: Window,
    *,
    days_into: int,
    blocked: Iterable[Window] = (),
    valid_to: date | None = None,
) -> date | NoWindow:
    """``days_into`` days after the period start, else the midpoint; strictly inside the period."""
    if period.end <= period.start + timedelta(days=1):
        return NoWindow(f"period {period.start}..{period.end} has no interior day")
    chosen = period.start + timedelta(days=days_into)
    if not chosen < period.end:
        span = (period.end - period.start).days
        chosen = period.start + timedelta(days=max(1, span // 2))
    if not period.start < chosen < period.end:
        return NoWindow(f"{chosen} is not strictly inside {period.start}..{period.end}")
    for w in blocked:
        if w.covers(chosen):
            return NoWindow(f"{chosen} is covered by a scheduled period {w.start}..{w.end}")
    if valid_to is not None and chosen > valid_to:
        return NoWindow(f"{chosen} is after the validity end {valid_to}")
    return chosen
