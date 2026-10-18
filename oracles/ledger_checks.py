"""Structural checks over a schedule of ledger rows (installments, invoices, transactions).

Each check returns a list of human-readable problems (empty = holds), so a test
can run all of them and report everything at once:

* :func:`additive_identity` — each row's parts add up to its total;
* :func:`contiguous` — periods follow each other with no gap or overlap;
* :func:`steps_match_term` — every period is exactly the expected number of months;
* :func:`allowed_values` — a field only takes values from a known set;
* :func:`sequence` — row codes follow an expected order (first row special, then a repeating code);
* :func:`renewal_row_starts_on` — one renewal row, starting on the previous term end;
* :func:`collects_indexed_amount` — the renewal row collects the indexed, not the old, amount.


:class:`ChargeLegs` reads the money legs of ONE booked row (price net/tax, fee
net/tax, total) and keeps two questions apart that a plain ``fee == 0`` check
merges: is the fee leg *present*, and does the row *charge no fee*? An absent
fee leg counts as zero, so :attr:`ChargeLegs.unexplained_total` (the part of the
total the price legs do not explain) is what still catches a fee that vanished
from the payload while its money stayed in the total.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from oracles.amounts import parse_json_amount

Row = Mapping[str, Any]
CENT = Decimal("0.01")


def _months_after(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    for day in (d.day, 30, 29, 28):
        try:
            return date(year, month, day)
        except ValueError:
            continue
    raise ValueError(d)  # pragma: no cover


def additive_identity(
    rows: Iterable[Row], parts: Sequence[str], total: str, tolerance: Decimal = Decimal("0.01")
) -> list[str]:
    problems = []
    for i, row in enumerate(rows):
        values = [row.get(p) for p in parts]
        if any(v is None for v in values) or row.get(total) is None:
            problems.append(f"row {i}: missing one of {[*parts, total]}")
            continue
        summed = sum((Decimal(str(v)) for v in values), Decimal(0))
        if abs(summed - Decimal(str(row[total]))) > tolerance:
            problems.append(f"row {i}: {' + '.join(parts)} = {summed} but {total} = {row[total]}")
    return problems


def contiguous(rows: Sequence[Row], start: str = "start", end: str = "end") -> list[str]:
    """Each period starts the day after the previous one ends (ends are inclusive)."""
    problems = []
    for i in range(1, len(rows)):
        prev_end, cur_start = rows[i - 1][end], rows[i][start]
        if cur_start != prev_end + timedelta(days=1):
            kind = "gap" if cur_start > prev_end + timedelta(days=1) else "overlap"
            problems.append(f"row {i}: {kind} — previous ends {prev_end}, this starts {cur_start}")
    return problems


def steps_match_term(
    rows: Iterable[Row], term_months: int, start: str = "start", end: str = "end"
) -> list[str]:
    problems = []
    for i, row in enumerate(rows):
        expected_end = _months_after(row[start], term_months) - timedelta(days=1)
        if row[end] != expected_end:
            problems.append(f"row {i}: {row[start]}..{row[end]} is not {term_months} month(s)")
    return problems


def allowed_values(rows: Iterable[Row], field: str, allowed: Iterable[Any]) -> list[str]:
    known = set(allowed)
    return [
        f"row {i}: {field}={row.get(field)!r} not in {sorted(map(str, known))}"
        for i, row in enumerate(rows)
        if row.get(field) not in known
    ]


def sequence(rows: Sequence[Row], field: str, first: Any, then: Any) -> list[str]:
    """The first row carries ``first``; every later row carries ``then``."""
    problems = []
    for i, row in enumerate(rows):
        want = first if i == 0 else then
        if row.get(field) != want:
            problems.append(f"row {i}: {field}={row.get(field)!r}, expected {want!r}")
    return problems


@dataclass(frozen=True)
class ChargeLegs:
    """The money legs of one booked row. ``None`` means the leg was not in the payload."""

    price_net: Decimal | None
    price_tax: Decimal | None
    fee_net: Decimal | None
    fee_tax: Decimal | None
    total: Decimal | None

    @classmethod
    def of(
        cls,
        row: Row,
        *,
        price_net: str = "net",
        price_tax: str = "tax",
        fee_net: str = "fee_net",
        fee_tax: str = "fee_tax",
        total: str = "total",
    ) -> ChargeLegs:
        """Read the legs from a JSON row; field names are configurable per payload shape."""

        def leg(name: str) -> Decimal | None:
            return parse_json_amount(row.get(name))

        return cls(leg(price_net), leg(price_tax), leg(fee_net), leg(fee_tax), leg(total))

    @property
    def price_and_total_read(self) -> bool:
        """The price legs and the total were all present: the row can be judged at all."""
        return None not in (self.price_net, self.price_tax, self.total)

    @property
    def fee_leg_present(self) -> bool:
        return self.fee_net is not None or self.fee_tax is not None

    @property
    def fee_net_or_zero(self) -> Decimal:
        return self.fee_net if self.fee_net is not None else Decimal(0)

    @property
    def fee_tax_or_zero(self) -> Decimal:
        return self.fee_tax if self.fee_tax is not None else Decimal(0)

    @property
    def unexplained_total(self) -> Decimal | None:
        """Total minus the price legs: whatever the row charges beyond the price itself.

        ``None`` when the price legs or the total could not be read.
        """
        if self.total is None or self.price_net is None or self.price_tax is None:
            return None
        return self.total - self.price_net - self.price_tax

    def fee_legs_explain_total(self, tolerance: Decimal = CENT) -> bool:
        """The fee legs (absent = zero) account for everything the price legs do not."""
        unexplained = self.unexplained_total
        if unexplained is None:
            return False
        return abs(unexplained - self.fee_net_or_zero - self.fee_tax_or_zero) <= tolerance

    def charges_no_fee(self, tolerance: Decimal = CENT) -> bool:
        """No fee on the fee legs AND none hidden in the total. Unreadable rows never pass."""
        unexplained = self.unexplained_total
        if unexplained is None:
            return False
        return (
            abs(self.fee_net_or_zero) <= tolerance
            and abs(self.fee_tax_or_zero) <= tolerance
            and abs(unexplained) <= tolerance
        )

    def describe(self) -> str:
        presence = "present" if self.fee_leg_present else "absent (counted as zero)"
        return (
            f"price net {self.price_net}, price tax {self.price_tax}, fee net {self.fee_net}, "
            f"fee tax {self.fee_tax}, total {self.total}; fee leg {presence}; "
            f"unexplained by the price legs: {self.unexplained_total}"
        )


def renewal_row_starts_on(
    rows: Sequence[Row],
    previous_term_end: date,
    *,
    kind: str = "kind",
    renewal_kind: Any = "RENEW",
    start: str = "start",
) -> list[str]:
    """Exactly one renewal row exists and it starts on the previous term's end date."""
    renewals = [row for row in rows if row.get(kind) == renewal_kind]
    if len(renewals) != 1:
        return [f"expected one {kind}={renewal_kind!r} row, found {len(renewals)}"]
    begins = renewals[0].get(start)
    if begins != previous_term_end:
        return [f"renewal row starts {begins}, expected the previous term end {previous_term_end}"]
    return []


def collects_indexed_amount(
    row: Row,
    indexed: Decimal,
    pre_index: Decimal,
    *,
    field: str = "net",
    tolerance: Decimal = Decimal("0.01"),
) -> list[str]:
    """The renewal row collects the indexed amount, and says so when it collects the old one."""
    raw = row.get(field)
    if raw is None:
        return [f"renewal row has no {field}"]
    value = Decimal(str(raw))
    if abs(value - indexed) <= tolerance:
        return []
    if abs(value - pre_index) <= tolerance:
        return [f"renewal row collects the pre-index {field} {value}, expected indexed {indexed}"]
    return [f"renewal row {field} {value} is neither indexed {indexed} nor pre-index {pre_index}"]
