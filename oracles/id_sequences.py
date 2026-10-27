"""Business-number oracle: shape, strict increase in issue order, uniqueness, stability.

Generated business numbers (``SUB-000123``, ``ORD-EU-004211``) promise three
things a test can check from the outside:

* **shape** — prefix, optional segment, zero-padded counter (:func:`parse`);
* **strict increase** — numbers drawn later from the same series are larger.
  Gaps are fine (other users draw from the same counter), a repeat or a step
  back is not (:func:`strict_increase_violations`);
* **one entity, one number** — no number is held by two entities
  (:func:`shared_numbers`), and an entity keeps its number across versions
  (:func:`unstable_entities`); a second number on a later version means the
  "draw once" guard let a re-entry draw again.

A fourth promise holds across a migration: numbers the new system issues all
sit above the highest number of the migrated records
(:func:`handover_violations`). It is only meaningful over the complete list,
so :func:`require_complete` refuses a read that came back short of the
reported total, and the check abstains when either origin is absent.

The shape is a caller-supplied regex with named groups ``prefix``,
``segment`` (optional) and ``counter``. Numbers in different series
(prefix + segment) are never compared with each other.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from verification.evidence_verdict import EvidenceVerdict

DEFAULT_PADDED_LENGTH = 6
DEFAULT_PATTERN = re.compile(r"^(?P<prefix>[A-Z]+)-(?:(?P<segment>[A-Z0-9]+)-)?(?P<counter>\d+)$")


@dataclass(frozen=True)
class BusinessNumber:
    raw: str
    prefix: str
    segment: str | None
    counter: int

    @property
    def series(self) -> tuple[str, str | None]:
        return (self.prefix, self.segment)


def parse(
    raw: str | None,
    *,
    expected_prefix: str | None = None,
    pattern: re.Pattern[str] = DEFAULT_PATTERN,
    padded_length: int = DEFAULT_PADDED_LENGTH,
) -> BusinessNumber | None:
    """The parsed number, or ``None`` when ``raw`` does not have the shape.

    Rejected: no match, a different prefix, a counter shorter than
    ``padded_length``, or a counter longer than it that still has a leading
    zero (padding only ever fills up to the length, never beyond).
    """
    if raw is None:
        return None
    m = pattern.match(raw.strip())
    if not m:
        return None
    prefix, digits = m.group("prefix"), m.group("counter")
    if expected_prefix is not None and prefix != expected_prefix:
        return None
    if len(digits) < padded_length or (len(digits) > padded_length and digits[0] == "0"):
        return None
    segment = m.groupdict().get("segment")
    return BusinessNumber(raw.strip(), prefix, segment, int(digits))


def strict_increase_violations(in_issue_order: Sequence[BusinessNumber]) -> list[str]:
    """Where a series fails to rise strictly, judged per series in issue order."""
    last: dict[tuple[str, str | None], BusinessNumber] = {}
    violations = []
    for number in in_issue_order:
        previous = last.get(number.series)
        if previous is not None and number.counter <= previous.counter:
            violations.append(f"{number.raw} issued after {previous.raw}")
        last[number.series] = number
    return violations


def shared_numbers(number_by_entity: Mapping[str, str]) -> dict[str, list[str]]:
    """number -> entities, for numbers held by more than one entity."""
    by_number: dict[str, set[str]] = {}
    for entity, number in number_by_entity.items():
        by_number.setdefault(number, set()).add(entity)
    return {n: sorted(e) for n, e in by_number.items() if len(e) > 1}


def unstable_entities(numbers_by_entity: Mapping[str, Iterable[str]]) -> dict[str, list[str]]:
    """entity -> its distinct numbers, for entities whose versions carry more than one."""
    unstable = {}
    for entity, numbers in numbers_by_entity.items():
        distinct = sorted(set(numbers))
        if len(distinct) > 1:
            unstable[entity] = distinct
    return unstable


@dataclass(frozen=True)
class Issued:
    """One entity as read back: its id and the number on each of its versions, oldest first."""

    entity: str
    numbers: Sequence[str]


@dataclass
class SequenceReport:
    malformed: list[str] = field(default_factory=list)
    increase_violations: list[str] = field(default_factory=list)
    shared: dict[str, list[str]] = field(default_factory=dict)
    unstable: dict[str, list[str]] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not (self.malformed or self.increase_violations or self.shared or self.unstable)

    def problems(self) -> list[str]:
        out = [f"malformed number: {m}" for m in self.malformed]
        out += self.increase_violations
        out += [f"{n} held by {', '.join(e)}" for n, e in self.shared.items()]
        out += [f"{e} carries {', '.join(n)}" for e, n in self.unstable.items()]
        return out


def check_sequence(
    in_issue_order: Sequence[Issued],
    *,
    expected_prefix: str | None = None,
    pattern: re.Pattern[str] = DEFAULT_PATTERN,
    padded_length: int = DEFAULT_PADDED_LENGTH,
) -> SequenceReport:
    """Run every check over entities listed in the order they were created.

    An entity's first-version number is its issued number for the increase
    check; every version's number counts for stability.
    """
    report = SequenceReport()
    first_numbers: list[BusinessNumber] = []
    first_by_entity: dict[str, str] = {}
    for item in in_issue_order:
        present = [n for n in item.numbers if n]
        for raw in present:
            if (
                parse(
                    raw,
                    expected_prefix=expected_prefix,
                    pattern=pattern,
                    padded_length=padded_length,
                )
                is None
            ):
                report.malformed.append(f"{item.entity}: {raw!r}")
        if not present:
            report.malformed.append(f"{item.entity}: no number")
            continue
        first = parse(
            present[0],
            expected_prefix=expected_prefix,
            pattern=pattern,
            padded_length=padded_length,
        )
        if first is not None:
            first_numbers.append(first)
        first_by_entity[item.entity] = present[0]
    report.increase_violations = strict_increase_violations(first_numbers)
    report.shared = shared_numbers(first_by_entity)
    report.unstable = unstable_entities(
        {i.entity: [n for n in i.numbers if n] for i in in_issue_order}
    )
    return report


# ---------------------------------------------------------------------------
# hand-over: new numbering continues above migrated numbering
# ---------------------------------------------------------------------------

_TRAILING_COUNTER = re.compile(r"(\d+)\D*$")


class IncompleteRead(AssertionError):
    """The listing returned fewer (or more) records than it reported in total."""


def parse_counter(raw: str) -> int:
    """The trailing counter of a business number (``SUB-000123`` -> 123); raises when none."""
    m = _TRAILING_COUNTER.search(str(raw or "").strip())
    if m is None:
        raise ValueError(f"{raw!r} carries no numeric counter")
    return int(m.group(1))


def require_complete(read: int, total: int) -> None:
    """Raise :class:`IncompleteRead` unless every reported record was read."""
    if read != total:
        raise IncompleteRead(
            f"read {read} of {total} reported records: a hand-over check over a partial list "
            "can miss exactly the number that breaks it"
        )


def handover_violations(
    numbers: Iterable[str],
    origin_of: Callable[[str], str | None],
    legacy: str,
    new: str,
    compare: Callable[[str], int] = parse_counter,
    *,
    verdict: EvidenceVerdict | None = None,
) -> list[str]:
    """New-origin numbers at or below the highest legacy number, as worded findings.

    Abstains (``no_verdict``) when either origin has no numbers: with nothing on
    one side, "every new number is above the migrated ones" is vacuous.
    """
    by_origin: dict[str, list[str]] = {legacy: [], new: []}
    for number in numbers:
        origin = origin_of(number)
        if origin in by_origin:
            by_origin[origin].append(number)
    empty = [o for o, found in by_origin.items() if not found]
    if empty:
        coverage = verdict or EvidenceVerdict(f"numbering hand-over {legacy} -> {new}")
        coverage.no_verdict(f"no {' or '.join(repr(o) for o in empty)} numbers to compare")
    highest = max(by_origin[legacy], key=compare)
    ceiling = compare(highest)
    return [
        f"{n} ({new}) is not above the highest {legacy} number {highest}"
        for n in by_origin[new]
        if compare(n) <= ceiling
    ]
