"""Fault switches: invented defects the demo can be told to exhibit.

Every switch is off by default. The active set is reported on **every** response (the
``X-Demo-Faults`` header, and a banner on every HTML screen), so a detector's evidence
always shows which defects were switched on while it ran. A detector that "passes" against a
demo with its fault off is therefore visibly a pass against a healthy system, never a pass
by construction.
"""

from __future__ import annotations

import threading

#: name -> what the defect does when switched on.
FAULTS: dict[str, str] = {
    "api_unavailable": "The API and the gateway answer 503 to everything; screens show the "
    "outage banner instead of their content.",
    "negative_price_accepted": "The price calculation lets a net amount below zero through.",
    "rounding_drift": "The price calculation cuts amounts off at the cent instead of rounding "
    "half up.",
    "screen_filter_unlabelled": "The filter field on the subscription list has neither a label "
    "nor an accessible name.",
    # one invented defect class each
    "refund_tax_applied_twice": "A closure credit computes its tax leg from the gross unearned "
    "amount, so tax is charged on tax.",
    "refund_legs_rounded_independently": "A closure credit rounds its total from the unrounded "
    "sum while net and tax are rounded on their own, so the legs can miss the total by a cent.",
    "proration_floors_to_zero": "A mid-period price change counts the remaining part of the "
    "period in whole periods, so it books nothing.",
    "effective_date_day_month_swapped": "A start-date change reads day and month the other way "
    "round whenever both are 12 or less.",
    "date_parser_rejects_leap_day": "A start date of 29 February is refused as an invalid date.",
    "index_percent_truncated": "Renewal indexation drops the decimals of the index percentage.",
    "payment_replay_double_counted": "Paying an invoice twice with the same idempotency key "
    "records two payments.",
    "message_send_fails_on_non_ascii": "Sending writes the text attachment as ASCII, so a "
    "customer name with a non-ASCII letter makes the send fail.",
    "xml_escapes_twice": "Outbound XML escapes invoice-note text twice.",
    "run_next_rebooks_claimed_job": "Booking the next transaction does not claim it first, so a "
    "second call in the same clock tick books it again.",
    "customer_read_cached_after_update": "Reading one customer returns the record as it was "
    "before its last update.",
    "customer_name_truncated": "Display names longer than 40 characters are stored cut to 40, "
    "without an error.",
    "role_history_sorted_as_text": "Role history is ordered by event id compared as text.",
    "reseller_share_scaled_twice": "Reseller terms store the share percent as a fraction and "
    "settlement then applies that fraction as a percent.",
    "document_renders_previous_version": "The plan update notice is rendered from the version "
    "before the change.",
    "doc_optional_prints_none": "The account-opened letter prints 'Billing contact: None' when "
    "there is no billing contact.",
    "doc_amount_unformatted": "The account-opened letter prints the charge per period as a bare "
    "number instead of a money amount.",
    "form_resets_select_after_error": "After a validation error on the closure screen the "
    "reason list jumps back to its first option.",
    "create_throttled_every_other": "Every second subscription create answers 429 and stores "
    "nothing.",
}


class UnknownFault(KeyError):
    """Raised for a switch name that is not in :data:`FAULTS`."""


class FaultSwitches:
    """Thread-safe set of active fault names."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: set[str] = set()

    def active(self) -> list[str]:
        with self._lock:
            return sorted(self._active)

    def is_on(self, name: str) -> bool:
        if name not in FAULTS:
            raise UnknownFault(name)
        with self._lock:
            return name in self._active

    def set(self, changes: dict[str, bool]) -> list[str]:
        unknown = sorted(set(changes) - set(FAULTS))
        if unknown:
            raise UnknownFault(", ".join(unknown))
        with self._lock:
            for name, on in changes.items():
                if on:
                    self._active.add(name)
                else:
                    self._active.discard(name)
            return sorted(self._active)

    def replace(self, names: list[str]) -> list[str]:
        """Make exactly ``names`` active."""
        unknown = sorted(set(names) - set(FAULTS))
        if unknown:
            raise UnknownFault(", ".join(unknown))
        with self._lock:
            self._active = set(names)
            return sorted(self._active)

    def reset(self) -> None:
        with self._lock:
            self._active.clear()

    def header(self) -> str:
        """Value for ``X-Demo-Faults``: ``none`` or a comma-separated sorted list."""
        names = self.active()
        return ",".join(names) if names else "none"

    def catalogue(self) -> list[dict[str, object]]:
        active = set(self.active())
        return [
            {"name": name, "description": text, "active": name in active}
            for name, text in sorted(FAULTS.items())
        ]
