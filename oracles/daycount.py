"""Day-count conventions and pro-rata refund oracles that do not depend on the convention.

Year fractions (:func:`year_fraction`) for the common conventions:

==================  ==========================================================
``ACT_365F``        actual days / 365
``ACT_360``         actual days / 360
``THIRTY_360_ISDA`` 30/360 bond basis (ISDA 2006 4.16(f)): D1 = min(D1, 30);
                    D2 capped at 30 **only when D1 is already 30**
``THIRTY_360_US``   30/360 US (SIA): bond basis plus the end-of-February rules
``THIRTY_E_360``    30E/360 Eurobond (4.16(g)): D1 and D2 capped at 30
                    **unconditionally**
``THIRTY_E_360_ISDA`` 30E/360 ISDA (4.16(h)): any month-end becomes 30 (except a
                    February maturity date)
==================  ==========================================================

``THIRTY_360_ISDA`` and ``THIRTY_E_360`` agree on almost every date and differ
only when the *later* date is the 31st and the earlier one is not already 30.
That is exactly the kind of difference that survives review: a test pinned to
one convention against an engine using the other goes red only on runs whose
end date falls on a 31st. Test the 31st explicitly.

**Refunds.** The unused slice of an already-charged period is computed per
convention (:func:`unused_fraction`), and :func:`candidate_refunds` evaluates
all of them. Where the engine's convention is not documented, assert only the
convention-INDEPENDENT bounds (:func:`assert_pro_rata_refund`): the refund is
negative, strictly smaller than the full period, correctly taxed when a rate is
given, and consistent with the remaining days under *some* modelled
convention. Fixing a single convention in a gating test would fail builds over
a refund that a closely related convention computes correctly.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum

CENT = Decimal("0.01")


class DayCount(str, Enum):
    ACT_ACT = "ACT/ACT"
    ACT_365F = "ACT/365F"
    ACT_360 = "ACT/360"
    THIRTY_360_ISDA = "30/360 ISDA"
    THIRTY_360_US = "30/360 US"
    THIRTY_E_360 = "30E/360"
    THIRTY_E_360_ISDA = "30E/360 ISDA"


#: Conventions modelled for the unused slice of a period (365F/360 are year bases, not slices).
PERIOD_CONVENTIONS = (
    DayCount.ACT_ACT,
    DayCount.THIRTY_360_ISDA,
    DayCount.THIRTY_360_US,
    DayCount.THIRTY_E_360,
    DayCount.THIRTY_E_360_ISDA,
)


def _last_of_month(d: date) -> bool:
    return d.day == calendar.monthrange(d.year, d.month)[1]


def _last_of_feb(d: date) -> bool:
    return d.month == 2 and _last_of_month(d)


def days_30_360(
    start: date, end: date, convention: DayCount, *, end_is_maturity: bool = False
) -> int:
    """Day count between two dates under a 30/360-family convention."""
    d1, d2 = start.day, end.day
    if convention is DayCount.THIRTY_360_ISDA:
        d1 = min(d1, 30)
        if d1 == 30 and d2 == 31:
            d2 = 30
    elif convention is DayCount.THIRTY_360_US:
        if _last_of_feb(start) and _last_of_feb(end):
            d2 = 30
        if _last_of_feb(start):
            d1 = 30
        if d2 == 31 and d1 >= 30:
            d2 = 30
        d1 = min(d1, 30)
    elif convention is DayCount.THIRTY_E_360:
        d1, d2 = min(d1, 30), min(d2, 30)
    elif convention is DayCount.THIRTY_E_360_ISDA:
        if _last_of_month(start):
            d1 = 30
        if _last_of_month(end) and not (end.month == 2 and end_is_maturity):
            d2 = 30
    else:
        raise ValueError(f"{convention} is not a 30/360 convention")
    return 360 * (end.year - start.year) + 30 * (end.month - start.month) + (d2 - d1)


def year_fraction(start: date, end: date, convention: DayCount) -> Decimal:
    """Year fraction between two dates. ACT/ACT needs a reference period: use unused_fraction."""
    if convention is DayCount.ACT_365F:
        return Decimal((end - start).days) / Decimal(365)
    if convention is DayCount.ACT_360:
        return Decimal((end - start).days) / Decimal(360)
    if convention is DayCount.ACT_ACT:
        raise ValueError("ACT/ACT year fractions need a reference period; use unused_fraction")
    return Decimal(days_30_360(start, end, convention)) / Decimal(360)


@dataclass(frozen=True)
class Period:
    """A charged period and the termination date inside it.

    ``end`` is the period's last covered day when ``end_inclusive`` (the usual
    billing shape: 2030-01-01..2030-01-31), else the first day of the next period.
    The termination date is the first day no longer covered.
    """

    start: date
    end: date
    termination: date
    end_inclusive: bool = True

    @property
    def next_start(self) -> date:
        return self.end + timedelta(days=1) if self.end_inclusive else self.end

    @property
    def is_mid_period(self) -> bool:
        return self.start < self.termination < self.next_start


def unused_fraction(period: Period, convention: DayCount) -> Decimal:
    """Fraction of the period left unused from the termination date, under ``convention``.

    The denominator is the period's own length measured start → next start, so a
    period starting on the 31st still counts as a whole term under 30/360 rules
    (measuring to the inclusive end instead would lose a day there).
    """
    if convention is DayCount.ACT_ACT:
        total = (period.next_start - period.start).days
        unused = (period.next_start - period.termination).days
    elif convention in PERIOD_CONVENTIONS:
        total = days_30_360(period.start, period.next_start, convention)
        unused = total - days_30_360(period.start, period.termination, convention)
    else:
        raise ValueError(f"{convention} is a year basis, not a period convention")
    if total <= 0:
        return Decimal(0)
    return (Decimal(unused) / Decimal(total)).quantize(Decimal("1e-10"), ROUND_HALF_UP)


def candidate_refund(period: Period, full_period_amount: Decimal, convention: DayCount) -> Decimal:
    """The refund under ``convention``, NEGATIVE: money flows back to the customer."""
    value = abs(full_period_amount) * unused_fraction(period, convention)
    return -value.quantize(CENT, ROUND_HALF_UP)


def candidate_refunds(period: Period, full_period_amount: Decimal) -> dict[DayCount, Decimal]:
    return {c: candidate_refund(period, full_period_amount, c) for c in PERIOD_CONVENTIONS}


def matched_conventions(
    period: Period, full_period_amount: Decimal, observed: Decimal | None, epsilon: Decimal = CENT
) -> list[DayCount]:
    """Every convention whose candidate is within ``epsilon`` of ``observed`` — plural."""
    if observed is None:
        return []
    return [
        c
        for c, v in candidate_refunds(period, full_period_amount).items()
        if abs(v - observed) <= epsilon
    ]


def describe_match(
    period: Period, full_period_amount: Decimal, observed: Decimal | None, epsilon: Decimal = CENT
) -> str:
    """How a match must be written down. Several matches never read as evidence for one of them."""
    matches = matched_conventions(period, full_period_amount, observed, epsilon)
    if not matches:
        return "no modelled convention matches"
    if len(matches) == 1:
        return f"{matches[0].value} only — this period pins the convention"
    names = ", ".join(m.value for m in matches)
    return (
        f"{names}: identical result for this period, so this run cannot tell them apart; "
        "a period that ends on the 31st or begins at month-end would"
    )


def within_pro_rata_bounds(refund: Decimal | None, full_period_amount: Decimal) -> bool:
    """Negative and strictly smaller in magnitude than the full period. Zero fails both halves."""
    return refund is not None and refund < 0 and abs(refund) < abs(full_period_amount)


def observed_fraction(refund: Decimal | None, full_period_amount: Decimal) -> Decimal | None:
    if refund is None or full_period_amount == 0:
        return None
    return abs(refund) / abs(full_period_amount)


def fraction_range(period: Period) -> tuple[Decimal, Decimal]:
    fractions = [unused_fraction(period, c) for c in PERIOD_CONVENTIONS]
    return min(fractions), max(fractions)


def fraction_within_modelled_range(
    period: Period, refund: Decimal | None, full_period_amount: Decimal, tolerance: Decimal
) -> bool:
    """The refunded fraction fits the remaining days under at least one modelled convention.

    This catches a refund computed over the wrong period (negative, non-zero,
    correctly taxed — and still wrong) without pinning the convention.
    """
    observed = observed_fraction(refund, full_period_amount)
    if observed is None:
        return False
    low, high = fraction_range(period)
    return low - tolerance <= observed <= high + tolerance


def describe_pro_rata(period: Period, full_period_amount: Decimal, refund: Decimal | None) -> str:
    low, high = fraction_range(period)
    candidates = " ".join(
        f"{c.value}={v}" for c, v in candidate_refunds(period, full_period_amount).items()
    )
    return (
        f"period {period.start}..{period.end}, terminated {period.termination}, "
        f"full-period amount {full_period_amount}, refund {refund} "
        f"(fraction {observed_fraction(refund, full_period_amount)}, band {low}..{high}). "
        f"Candidates: {candidates}"
    )


def assert_pro_rata_refund(
    context: str,
    period: Period,
    full_period_amount: Decimal,
    refund: Decimal | None,
    *,
    refund_tax: Decimal | None = None,
    tax_rate: Decimal | None = None,
    tolerance: Decimal = CENT,
) -> None:
    """The convention-independent assertion for a mid-period refund."""
    detail = " " + describe_pro_rata(period, full_period_amount, refund)
    assert within_pro_rata_bounds(refund, full_period_amount), (
        f"{context}: the refund must be negative and strictly smaller than the full period, "
        f"but was {refund}. Zero means the paid period was skipped outright.{detail}"
    )
    if tax_rate is not None:
        expected_tax = (abs(refund) * tax_rate).quantize(CENT, ROUND_HALF_UP)
        assert refund_tax is not None and abs(abs(refund_tax) - expected_tax) <= tolerance, (
            f"{context}: tax on the refund must be {tax_rate} x net = {expected_tax}, "
            f"got {refund_tax}.{detail}"
        )
    # The fraction tolerance scales the cent tolerance to the period amount.
    fraction_tol = tolerance / abs(full_period_amount) if full_period_amount else tolerance
    assert fraction_within_modelled_range(period, refund, full_period_amount, fraction_tol), (
        f"{context}: the refunded slice is not consistent with the remaining days under any "
        f"modelled convention — a refund computed over the wrong period.{detail}"
    )
