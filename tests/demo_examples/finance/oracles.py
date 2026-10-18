"""Reseller-terms oracles for the finance examples.

A share percent is stored as sent: ``6`` is six percent, not ``0.06``. The oracle fails with
wording keyed to that defect (the signature a detector matches) and reports its near
misses - a missing value, a different number - with other wording, so they are never
counted as the tracked defect.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

SHARE_DEFECT = "DEMO-114"
SHARE_SIGNATURE = r"DEMO-114: the share percent was rescaled from \S+ to"


class _Missing:
    """The field is not in the response at all."""

    def __repr__(self) -> str:
        return "MISSING"


MISSING = _Missing()


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float | Decimal) and not isinstance(value, bool)


def read_share(terms: Mapping[str, Any]) -> Decimal | None | _Missing:
    """The stored share: a Decimal, ``None`` for a non-number, :data:`MISSING` when absent."""
    if "share_percent" not in terms:
        return MISSING
    value = terms["share_percent"]
    return Decimal(str(value)) if _is_number(value) else None


def assert_share_kept(terms: Mapping[str, Any], sent: Any) -> None:
    """The stored share percent equals what was sent."""
    expected = Decimal(str(sent))
    value = read_share(terms)
    if value is MISSING or value is None:
        raise AssertionError(f"the share percent cannot be read: {value!r}")
    if value == expected:
        return
    if expected and value == expected / 100:
        raise AssertionError(
            f"{SHARE_DEFECT}: the share percent was rescaled from {expected} to {value}"
        )
    raise AssertionError(f"the share percent reads {value}, sent {expected}")


def expected_commission(net: Decimal, share: Decimal, flat_fee: Decimal) -> Decimal:
    """What a reseller earns on one booked period: ``net x share / 100 + flat fee``."""
    return (net * share / 100 + flat_fee).quantize(Decimal("0.01"))
