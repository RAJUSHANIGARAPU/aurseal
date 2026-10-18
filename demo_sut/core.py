"""Shared pieces: errors, settings, the per-instance context, money and calendar helpers."""

from __future__ import annotations

import calendar
import copy
import threading
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from typing import Any

from demo_sut.clock import DemoClock
from demo_sut.faults import FaultSwitches
from demo_sut.store import Store

# -- errors ---------------------------------------------------------------


class DemoError(Exception):
    """An error the API turns into a JSON body ``{"error", "message", ...}``."""

    status = 400
    code = "bad_request"

    def __init__(self, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.message = message
        self.extra = extra

    def body(self) -> dict[str, Any]:
        return {"error": self.code, "message": self.message, **self.extra}


class BadRequest(DemoError):
    status, code = 400, "bad_request"


class Unauthorized(DemoError):
    status, code = 401, "unauthorized"


class NotFound(DemoError):
    """A missing resource behind a known route (the *service* 404)."""

    status, code = 404, "not_found"

    def __init__(self, resource: str, ident: Any) -> None:
        super().__init__(f"{resource} {ident} not found", resource=resource, id=str(ident))


class Conflict(DemoError):
    status, code = 409, "conflict"


class Invalid(DemoError):
    """A named validation failure; ``fields`` maps a field path to its message."""

    status, code = 422, "validation"

    def __init__(self, message: str, fields: dict[str, str] | None = None) -> None:
        super().__init__(message, fields=fields or {})


class Unavailable(DemoError):
    status, code = 503, "unavailable"


class Throttled(DemoError):
    """A refusal that stored nothing; ``retry_after`` seconds go into ``Retry-After``."""

    status, code = 429, "throttled"

    def __init__(self, message: str, retry_after: int = 0) -> None:
        super().__init__(message)
        self.retry_after = retry_after


# -- settings -------------------------------------------------------------

DEFAULT_SETTINGS: dict[str, Any] = {
    "auth_required": False,
    "background_delay_ms": 150,
    "commission_cap_percent": 20,
    "day_count": "actual/period",
    "deployed_at": "2000-01-01T00:00:00Z",
    "index_percent": 2.5,
    "message_id_scheme": "uuid7",
    "payment_term_days": 30,
    "renewal_window_days": 90,
    "require_verified_customer": True,
    "scheduler_mode": "all",
    "screening_label": "Run screening",
    "send_order": "v1",
    "ui_timezone": "Europe/Berlin",
    "account_check_mismatch_once": [],
}

SETTING_CHOICES: dict[str, tuple[Any, ...]] = {
    "day_count": ("actual/period", "30/360"),
    "message_id_scheme": ("uuid7", "uuid4"),
    "scheduler_mode": ("all", "one_at_a_time"),
    "screening_label": ("Run screening", "Start check"),
    "send_order": ("v1", "v2"),
}


@dataclass
class Context:
    """Everything one running demo instance owns."""

    store: Store
    clock: DemoClock = field(default_factory=DemoClock)
    faults: FaultSwitches = field(default_factory=FaultSwitches)
    settings: dict[str, Any] = field(default_factory=lambda: copy.deepcopy(DEFAULT_SETTINGS))
    run_tag: str | None = None
    actor: str = "anonymous"
    counters: dict[str, Any] = field(default_factory=dict)
    cache: dict[str, Any] = field(default_factory=dict)
    background: list[threading.Thread] = field(default_factory=list)

    def fault(self, name: str) -> bool:
        return self.faults.is_on(name)

    def tick(self, name: str) -> int:
        """A per-instance counter (used by intermittent faults)."""
        self.counters[name] = self.counters.get(name, 0) + 1
        return self.counters[name]

    def stamp(self) -> str:
        return self.clock.now().isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def update_settings(self, changes: dict[str, Any]) -> dict[str, Any]:
        unknown = sorted(set(changes) - set(DEFAULT_SETTINGS))
        if unknown:
            raise BadRequest(f"unknown settings: {', '.join(unknown)}")
        for key, value in changes.items():
            allowed = SETTING_CHOICES.get(key)
            if allowed and value not in allowed:
                raise BadRequest(f"{key} must be one of {list(allowed)}")
        self.settings.update(changes)
        return dict(self.settings)


# -- money ----------------------------------------------------------------

CENT = Decimal("0.01")


def money(value: Any, *, truncate: bool = False) -> Decimal:
    """Parse a number to 2 decimals, half-up (or truncating, for the rounding fault)."""
    try:
        amount = Decimal(str(value))
    except Exception as exc:  # any parse failure is a validation error
        raise Invalid("Amount is not a number.", {"amount": "Amount is not a number."}) from exc
    return amount.quantize(CENT, rounding=ROUND_DOWN if truncate else ROUND_HALF_UP)


def num(amount: Decimal) -> float:
    """Decimal -> JSON number with 2 decimals."""
    return float(amount.quantize(CENT, rounding=ROUND_HALF_UP))


def fmt_money(amount: Decimal | float) -> str:
    return f"EUR {Decimal(str(amount)).quantize(CENT, rounding=ROUND_HALF_UP):.2f}"


# -- calendar -------------------------------------------------------------

FREQUENCY_MONTHS = {"MONTHLY": 1, "QUARTERLY": 3, "YEARLY": 12}


def iso(day: date) -> str:
    return day.isoformat()


def parse_date(value: Any, field_name: str = "date") -> date:
    if not isinstance(value, str):
        raise Invalid(f"{field_name} must be an ISO date.", {field_name: "Must be an ISO date."})
    try:
        return date.fromisoformat(value[:10])
    except ValueError as exc:
        raise Invalid(
            f"{field_name} must be an ISO date.", {field_name: "Must be an ISO date."}
        ) from exc


def add_months(day: date, months: int) -> date:
    """Calendar month arithmetic with end-of-month clamping (31 Jan + 1 = 28/29 Feb)."""
    index = day.month - 1 + months
    year, month = day.year + index // 12, index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def add_years_clamped(day: date, years: int = 1) -> date:
    """One calendar year later; 29 Feb clamps to 28 Feb in a non-leap year."""
    return add_months(day, 12 * years)


def day_span(start: date, end: date, convention: str) -> int:
    """Days between two dates under the demo's day-count convention."""
    if convention == "30/360":
        d1, d2 = min(start.day, 30), min(end.day, 30) if start.day >= 30 else end.day
        return 360 * (end.year - start.year) + 30 * (end.month - start.month) + (d2 - d1)
    return (end - start).days


def overlap(a_start: date, a_end: date, b_start: date, b_end: date) -> tuple[date, date] | None:
    start, end = max(a_start, b_start), min(a_end, b_end)
    return (start, end) if start < end else None


ONE_DAY = timedelta(days=1)
