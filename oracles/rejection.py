"""Multi-surface rejection classifier: did the system refuse an operation, on any surface?

An out-of-range input or forbidden operation can be refused in several ways,
and a test that watches only one of them reports "accepted" when the refusal
came through another:

* ``API_STATUS`` — the call answered 4xx;
* ``FIELD_MESSAGE`` — an error message rendered at the field;
* ``CONTROL_FLAGGED`` — the control is flagged invalid with no text;
* ``BANNER_MESSAGE`` — a banner / toast / ``role=alert`` at submit time;
* ``VALUE_ADJUSTED`` — no message, but the value read back was pulled inside
  the allowed range (clamped or reverted);
* ``STATE_UNCHANGED`` — no message, but the state change the operation should
  have caused did not happen.

Reading the surfaces (a page object, an HTTP call) stays with the caller;
deciding lives here so it is testable offline. Two traps are guarded:

* ``VALUE_ADJUSTED`` fires only when the *entered* value was out of range and
  the read-back is inside it — otherwise every in-range entry would read as a
  correction.
* A blank or unparseable read-back is **not** a rejection signal: it is also
  exactly what a broken locator produces. Use a known-good positive control.

Messages already on screen before the operation are not signals; pass both
lists through :func:`appeared_since` (a multiset difference).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum

from oracles.amounts import parse_amount


class Signal(str, Enum):
    API_STATUS = "api-status"
    FIELD_MESSAGE = "inline-field-error"
    CONTROL_FLAGGED = "control-marked-invalid"
    BANNER_MESSAGE = "page-level-alert"
    VALUE_ADJUSTED = "value-corrected"
    STATE_UNCHANGED = "state-unchanged"


class ReadBack(str, Enum):
    INSIDE_LIMIT = "within-bound"
    BEYOND_LIMIT = "out-of-bound"
    BLANK = "blank"
    UNPARSEABLE = "unparseable"
    NOT_READ = "not-read"


@dataclass(frozen=True)
class Bound:
    minimum: Decimal | None = None
    maximum: Decimal | None = None

    @classmethod
    def at_most(cls, value: str) -> Bound:
        return cls(None, Decimal(value))

    @classmethod
    def at_least(cls, value: str) -> Bound:
        return cls(Decimal(value), None)

    def contains(self, value: Decimal | None) -> bool:
        if value is None:
            return False
        if self.minimum is not None and value < self.minimum:
            return False
        return self.maximum is None or value <= self.maximum


def appeared_since(before: Iterable[str], after: Iterable[str]) -> list[str]:
    """Entries of ``after`` not already in ``before``, compared as a multiset."""
    remaining = [b.strip() for b in before if b and b.strip()]
    appeared = []
    for text in (a.strip() for a in after if a and a.strip()):
        if text in remaining:
            remaining.remove(text)
        else:
            appeared.append(text)
    return appeared


def parse_displayed_number(text: str | None, locale: str = "nl_NL") -> Decimal | None:
    if text is None or not text.strip():
        return None
    cleaned = text.strip().rstrip("%").strip()
    try:
        return parse_amount(cleaned, locale)
    except ValueError:
        try:
            return Decimal(cleaned)
        except InvalidOperation:
            return None


@dataclass(frozen=True)
class RejectionSignals:
    entered: str = ""
    allowed: Bound | None = None
    read_back: str | None = None
    api_status: int | None = None
    inline_errors: list[str] = field(default_factory=list)
    control_invalid: bool = False
    page_alerts: list[str] = field(default_factory=list)
    state_changed: bool | None = None
    locale: str = "nl_NL"

    def entered_is_out_of_bound(self) -> bool:
        """Precondition for a range probe: an in-range input tests nothing."""
        value = parse_displayed_number(self.entered, self.locale)
        return self.allowed is not None and value is not None and not self.allowed.contains(value)

    @property
    def read_back_state(self) -> ReadBack:
        if self.read_back is None or self.allowed is None:
            return ReadBack.NOT_READ
        if not self.read_back.strip():
            return ReadBack.BLANK
        value = parse_displayed_number(self.read_back, self.locale)
        if value is None:
            return ReadBack.UNPARSEABLE
        return ReadBack.INSIDE_LIMIT if self.allowed.contains(value) else ReadBack.BEYOND_LIMIT

    @property
    def fired(self) -> list[Signal]:
        out = []
        if self.api_status is not None and 400 <= self.api_status < 500:
            out.append(Signal.API_STATUS)
        if any(e.strip() for e in self.inline_errors):
            out.append(Signal.FIELD_MESSAGE)
        if self.control_invalid:
            out.append(Signal.CONTROL_FLAGGED)
        if any(a.strip() for a in self.page_alerts):
            out.append(Signal.BANNER_MESSAGE)
        if self.entered_is_out_of_bound() and self.read_back_state is ReadBack.INSIDE_LIMIT:
            out.append(Signal.VALUE_ADJUSTED)
        if self.state_changed is False:
            out.append(Signal.STATE_UNCHANGED)
        return out

    @property
    def rejected(self) -> bool:
        return bool(self.fired)

    def describe(self) -> str:
        verdict = (
            "REJECTED via " + ", ".join(s.value for s in self.fired)
            if self.rejected
            else "ACCEPTED"
        )
        return (
            f"{verdict} (entered={self.entered!r}, read_back={self.read_back!r} "
            f"[{self.read_back_state.value}], api={self.api_status}, inline={self.inline_errors}, "
            f"invalid={self.control_invalid}, alerts={self.page_alerts}, "
            f"state_changed={self.state_changed})"
        )
