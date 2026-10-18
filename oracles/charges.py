"""Expected-value oracle for a charge schedule: per-term surcharge plus tax, and change deltas.

Many billing models charge a base amount per installment, add a surcharge that
depends on how often the customer pays, then add a tax. The rates are business
configuration, so :class:`ChargeSchedule` takes them as data rather than
hardcoding them; :meth:`ChargeSchedule.compute` returns net, tax and total
rounded half-up to cents, the way an invoice prints them.

For a mid-period change, the delta for a whole installment is exact, but the
delta for the rest of the period depends on the service's pro-rating and cannot
be derived here — so :meth:`ChargeSchedule.period_delta_ceiling` is an upper
bound (a strict ceiling), not an expected value. Use it as one.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def _cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, ROUND_HALF_UP)


@dataclass(frozen=True)
class Charge:
    gross: Decimal
    surcharge: Decimal
    net: Decimal
    tax: Decimal
    total: Decimal

    @property
    def additive(self) -> bool:
        return self.gross + self.surcharge == self.net and self.net + self.tax == self.total


@dataclass(frozen=True)
class ChargeSchedule:
    """``surcharge_by_term`` maps installment length in months to a rate (``{1: 0.06, 12: 0}``)."""

    surcharge_by_term: Mapping[int, Decimal]
    tax_rate: Decimal
    period_months: int = 12

    def surcharge_rate(self, term_months: int) -> Decimal:
        try:
            return Decimal(self.surcharge_by_term[term_months])
        except KeyError:
            raise ValueError(
                f"no surcharge rate for a {term_months}-month term; known: "
                f"{sorted(self.surcharge_by_term)}"
            ) from None

    def installments_per_period(self, term_months: int) -> int:
        if term_months <= 0 or self.period_months % term_months:
            raise ValueError(f"{term_months} does not divide a {self.period_months}-month period")
        return self.period_months // term_months

    def compute(self, gross: Decimal, term_months: int) -> Charge:
        surcharge = _cents(gross * self.surcharge_rate(term_months))
        net = gross + surcharge
        tax = _cents(net * self.tax_rate)
        return Charge(gross, surcharge, net, tax, net + tax)

    def installment_delta(self, old_gross: Decimal, new_gross: Decimal, term_months: int) -> Charge:
        """Exact delta of one whole installment when the gross changes."""
        old, new = self.compute(old_gross, term_months), self.compute(new_gross, term_months)
        return Charge(
            new.gross - old.gross,
            new.surcharge - old.surcharge,
            new.net - old.net,
            new.tax - old.tax,
            new.total - old.total,
        )

    def period_delta_ceiling(
        self, old_gross: Decimal, new_gross: Decimal, term_months: int
    ) -> Decimal:
        """Upper bound on |net delta| for the remainder of a period changed mid-way."""
        per_installment = abs(self.installment_delta(old_gross, new_gross, term_months).net)
        return per_installment * self.installments_per_period(term_months)


def sign_matches(change: Decimal, booked_delta: Decimal) -> bool:
    """An increase books a positive delta, a decrease a negative one, no change books zero."""
    return (
        (change > 0 and booked_delta > 0)
        or (change < 0 and booked_delta < 0)
        or (change == 0 and booked_delta == 0)
    )


# -- renewal indexation ---------------------------------------------------------


def indexed_amount(base: Decimal, index_factor: Decimal) -> Decimal:
    """``base x (1 + f)`` in cents: the price after indexation by factor ``f`` (0.025 = 2.5 %)."""
    return _cents(Decimal(base) * (1 + Decimal(index_factor)))


def bare_multiplier_misread(base: Decimal, index_factor: Decimal) -> Decimal:
    """The named misread ``base x f``: the factor applied as the whole price, not the increase.

    Keep it next to the correct figure so a failure can say which reading the system used.
    """
    return _cents(Decimal(base) * Decimal(index_factor))


def truncated_percent_misread(base: Decimal, index_factor: Decimal) -> Decimal:
    """The named misread where the index percentage loses its decimals (2.5 % read as 2 %)."""
    whole_percent = int(Decimal(index_factor) * 100)
    return _cents(Decimal(base) * (1 + Decimal(whole_percent) / 100))


#: Each named reading of an indexed price, in words a failure message can quote.
READINGS = {
    "indexed": "indexed as specified",
    "bare_multiplier_misread": "the factor applied as the whole price",
    "percent_truncated_misread": "index percent truncated to a whole number",
    "not_indexed": "the price was not indexed at all",
    "unexplained": "no known reading",
}


def indexed_renewal(
    schedule: ChargeSchedule, base: Decimal, index_factor: Decimal, term_months: int
) -> Charge:
    """Every figure of one renewed installment: index the base, then surcharge, net, tax, total.

    ``base`` is one month's pre-index amount; the installment covers ``term_months`` months.
    """
    return schedule.compute(indexed_amount(base, index_factor) * term_months, term_months)


def explain_indexed(observed: Decimal, base: Decimal, index_factor: Decimal) -> str:
    """Name the reading an observed indexed price matches: ``indexed``, a misread, or neither."""
    value = Decimal(str(observed))
    if value == indexed_amount(base, index_factor):
        return "indexed"
    if value == bare_multiplier_misread(base, index_factor):
        return "bare_multiplier_misread"
    if value == truncated_percent_misread(base, index_factor):
        return "percent_truncated_misread"
    if value == _cents(Decimal(base)):
        return "not_indexed"
    return "unexplained"
