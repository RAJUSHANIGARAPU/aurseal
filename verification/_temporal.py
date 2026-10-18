"""Calendar arithmetic shared by the equivalence oracle and the invariants."""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any


@dataclass(frozen=True)
class Shift:
    """A calendar offset: whole months first (clamped to month end), then days."""

    months: int = 0
    days: int = 0

    def apply(self, value: date) -> date:
        return add_months(value, self.months) + timedelta(days=self.days)

    def __str__(self) -> str:
        parts = [f"{n:+d} {unit}" for n, unit in ((self.months, "mo"), (self.days, "d")) if n]
        return " ".join(parts) or "+0 d"


def add_months(value: date, months: int) -> date:
    """Add calendar months; Jan 31 + 1 month is the last day of February."""
    if not months:
        return value
    index = value.month - 1 + months
    year, month = value.year + index // 12, index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def as_temporal(value: Any) -> date | None:
    """Coerce a ``date``/``datetime`` or an ISO-8601 string; ``None`` otherwise."""
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        if len(value) == 10:
            return date.fromisoformat(value)
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def add_years_clamped(value: date, years: int) -> date:
    """Add calendar years; 29 February clamps to 28 February in a non-leap year."""
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        return value.replace(year=value.year + years, day=28)
