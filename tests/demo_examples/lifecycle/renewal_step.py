"""Readers for the renewal indexation step, and the back-navigation guard.

The step shows the index percentage, the old and new term end (read-only) and the current
and new price. :class:`RenewalStep` reads those off a renewal draft; anything the draft does
not carry stays ``None`` rather than a guessed default. :func:`lost_after_round_trip`
compares the step before Back and after Next and names every required value that went
missing on the way - the shape of a step left stuck on a required-field error.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields
from decimal import Decimal
from typing import Any

from test_data.shape import ObservedShape

REQUIRED = ("index_percent", "old_term_end", "new_term_end", "current_price", "new_price")
READ_ONLY = ("old_term_end", "new_term_end")


@dataclass(frozen=True)
class RenewalStep:
    index_percent: Decimal | None
    old_term_end: str | None
    new_term_end: str | None
    current_price: Decimal | None
    new_price: Decimal | None

    @classmethod
    def read(cls, draft: Mapping[str, Any]) -> RenewalStep:
        shape = ObservedShape.observe(
            f"renewal draft {draft.get('id')}",
            draft,
            {
                "index_percent": "preview.index_percent",
                "old_term_end": "preview.old_term_end",
                "new_term_end": "preview.new_term_end",
                "current_price": "preview.current_monthly_net",
                "new_price": "preview.new_monthly_net",
            },
        )

        def dec(name: str) -> Decimal | None:
            value = shape.get(name)
            return None if value is None else Decimal(str(value))

        return cls(
            index_percent=dec("index_percent"),
            old_term_end=shape.get("old_term_end"),
            new_term_end=shape.get("new_term_end"),
            current_price=dec("current_price"),
            new_price=dec("new_price"),
        )

    def as_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}


def lost_after_round_trip(
    before: RenewalStep, after: RenewalStep, required: Sequence[str] = REQUIRED
) -> list[str]:
    """Required values present before Back and missing after Next (empty = the step held)."""
    was, now = before.as_dict(), after.as_dict()
    return [
        f"{name} lost on back/forward (was {was[name]})"
        for name in required
        if was[name] is not None and now[name] is None
    ]


def read_only_changed(before: RenewalStep, after: RenewalStep) -> list[str]:
    was, now = before.as_dict(), after.as_dict()
    return [
        f"{name} changed {was[name]} -> {now[name]}" for name in READ_ONLY if was[name] != now[name]
    ]
