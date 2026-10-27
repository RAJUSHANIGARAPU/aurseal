"""Declarative before/after invariants.

An operation's contract is often a relation between the state before and the
state after it ran: a date advanced by one period, an amount scaled by a factor,
nothing left pending. Writing those as ad-hoc asserts scatters the contract
across tests; declaring them makes it reusable and reports every broken rule at
once instead of stopping at the first.

    rules = InvariantSet(
        advanced_by("schedule.next_run", months=1),
        scaled_by("limits.max", factor=1.02, rel_tol=1e-9,
                  readings={"bare factor": lambda old: old * 0.02}),
        non_empty("history"),
        no_item_in_state("tasks", "status", "PENDING", "RETRYING"),
    )
    assert_invariants(before, after, rules)

``readings`` names the known *wrong* models of a value-changing rule. When the rule
misses, every reading that equals the observed value is appended to the reason
(``expected 102.0 (100.0 x 1.02), got 2.0 (bare factor)``), so the reader sees which
mistake was made instead of re-deriving it. ``unchanged`` is always one of them.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Iterator, Mapping, Sized
from dataclasses import dataclass
from typing import Any

from verification._paths import resolve
from verification._temporal import Shift, as_temporal

# A check returns None when the invariant holds, otherwise the reason it does not.
Check = Callable[[Any, Any], str | None]


@dataclass(frozen=True)
class Invariant:
    description: str
    check: Check

    def evaluate(self, before: Any, after: Any) -> Violation | None:
        try:
            reason = self.check(before, after)
        except KeyError as exc:
            reason = f"path not found: {exc.args[0]}"
        return None if reason is None else Violation(self.description, reason)


@dataclass(frozen=True)
class Violation:
    invariant: str
    reason: str

    def __str__(self) -> str:
        return f"{self.invariant}: {self.reason}"


class InvariantSet:
    """An ordered, composable collection of invariants (``a + b`` concatenates)."""

    def __init__(self, *invariants: Invariant | InvariantSet) -> None:
        self._items: tuple[Invariant, ...] = tuple(
            inv
            for item in invariants
            for inv in (item if isinstance(item, InvariantSet) else [item])
        )

    def __iter__(self) -> Iterator[Invariant]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __add__(self, other: Invariant | InvariantSet) -> InvariantSet:
        return InvariantSet(self, other)

    def check(self, before: Any, after: Any) -> list[Violation]:
        return [v for inv in self._items if (v := inv.evaluate(before, after)) is not None]


def check_invariants(
    before: Any, after: Any, invariants: Iterable[Invariant] | InvariantSet
) -> list[Violation]:
    return InvariantSet(*invariants).check(before, after)


def assert_invariants(
    before: Any, after: Any, invariants: Iterable[Invariant] | InvariantSet
) -> None:
    """Pytest helper: one ``AssertionError`` listing every violated invariant."""
    violations = check_invariants(before, after, invariants)
    if violations:
        lines = "\n".join(f"  - {v}" for v in violations)
        raise AssertionError(f"{len(violations)} invariant(s) violated:\n{lines}")


# ------------------------------------------------------------------
# factories
# ------------------------------------------------------------------

Reading = Callable[[Any], Any]
UNCHANGED = "unchanged"


def _readings(extra: Mapping[str, Reading] | None) -> dict[str, Reading]:
    return {UNCHANGED: lambda old: old, **(extra or {})}


def _matching_readings(
    old: Any, new: Any, readings: Mapping[str, Reading], same: Callable[[Any, Any], bool]
) -> str:
    """`` (label, label)`` for every known wrong reading that equals ``new``; "" if none."""
    labels = []
    for label, model in readings.items():
        try:
            if same(new, model(old)):
                labels.append(label)
        except (TypeError, ValueError, ArithmeticError):
            continue
    return f" ({', '.join(labels)})" if labels else ""


def advanced_by(
    path: str,
    *,
    months: int = 0,
    days: int = 0,
    readings: Mapping[str, Reading] | None = None,
) -> Invariant:
    """``after[path] == before[path] + period`` for a date/datetime field."""
    shift = Shift(months=months, days=days)
    known = _readings(readings)

    def check(before: Any, after: Any) -> str | None:
        old, new = as_temporal(resolve(before, path)), as_temporal(resolve(after, path))
        if old is None or new is None:
            return f"not a date (before={resolve(before, path)!r}, after={resolve(after, path)!r})"
        expected = shift.apply(old)
        if new == expected:
            return None
        named = _matching_readings(old, new, known, lambda a, b: a == b)
        return f"expected {expected}, got {new}{named} (before {old})"

    return Invariant(f"{path} advanced by {shift}", check)


def scaled_by(
    path: str,
    factor: float,
    *,
    abs_tol: float = 0.0,
    rel_tol: float = 0.0,
    readings: Mapping[str, Reading] | None = None,
) -> Invariant:
    """``after[path] == before[path] * factor`` within tolerance."""
    known = _readings(readings)

    def close(a: Any, b: Any) -> bool:
        return math.isclose(a, b, abs_tol=abs_tol, rel_tol=rel_tol)

    def check(before: Any, after: Any) -> str | None:
        old, new = resolve(before, path), resolve(after, path)
        expected = old * factor
        if close(new, expected):
            return None
        named = _matching_readings(old, new, known, close)
        return f"expected {expected!r} ({old!r} x {factor}), got {new!r}{named}"

    return Invariant(f"{path} scaled by {factor}", check)


def unchanged(path: str) -> Invariant:
    def check(before: Any, after: Any) -> str | None:
        old, new = resolve(before, path), resolve(after, path)
        return None if old == new else f"changed from {old!r} to {new!r}"

    return Invariant(f"{path} unchanged", check)


def changed(path: str) -> Invariant:
    def check(before: Any, after: Any) -> str | None:
        value = resolve(after, path)
        return None if resolve(before, path) != value else f"still {value!r}"

    return Invariant(f"{path} changed", check)


def non_empty(path: str) -> Invariant:
    """The collection at ``path`` in the *after* state has at least one element."""

    def check(_before: Any, after: Any) -> str | None:
        value = resolve(after, path)
        if not isinstance(value, Sized):
            return f"not a collection: {value!r}"
        return None if len(value) else "is empty"

    return Invariant(f"{path} non-empty", check)


def no_item_in_state(collection: str, field: str, *states: Any) -> Invariant:
    """No element of the *after* collection has ``field`` in ``states``."""
    if not states:
        raise ValueError("no_item_in_state needs at least one state")

    def check(_before: Any, after: Any) -> str | None:
        stuck = [
            (index, item.get(field))
            for index, item in enumerate(resolve(after, collection))
            if item.get(field) in states
        ]
        if not stuck:
            return None
        shown = ", ".join(f"[{i}]={s!r}" for i, s in stuck[:5])
        return f"{len(stuck)} item(s) left in {states}: {shown}"

    return Invariant(f"no {collection}[*].{field} in {states}", check)


def custom(description: str, check: Check) -> Invariant:
    return Invariant(description, check)
