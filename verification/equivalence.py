"""Twin equivalence oracle.

Two code paths that should produce the same result for the same input (old
and new implementation, sync and async route, API and batch job) are compared
field by field. The answer is a list of differences, each naming the path, both
values and the rule that judged it, so a red run explains itself.

Rules are matched by path pattern (see ``verification._paths``):

* ``ignore``      — volatile fields such as ids, timestamps and versions;
* ``tolerances``  — numeric fields compared within ``abs_tol``/``rel_tol``;
* ``date_shifts`` — date fields where ``right`` is expected at ``left`` + shift;
* ``keyed``       — lists paired by a business key (``{pattern: key_fn}``) instead of by
  position. Generated ids get renumbered and array order is creation order, not
  meaning: pairing ``invoice.lines`` by ``(code, period)`` compares the line that means
  the same thing on both sides. A key on one side only is ``missing_left``/``right``; a
  key that occurs twice on one side is a ``duplicate_key`` difference, never a guess.
  Keyed items render as ``lines.<SEATS|2026-01>``, so ``lines[*].id`` still addresses
  their ids.

Ignored paths are reported too: an oracle that ignores everything compares
nothing, and that should be visible in the output rather than look like a pass.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Hashable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any

from verification import _paths
from verification._paths import Path
from verification._temporal import Shift, as_temporal


class _Absent:
    def __repr__(self) -> str:
        return "<absent>"


ABSENT: Any = _Absent()
"""Placeholder for the side of a ``missing_*`` difference that has no value."""


class DiffKind(str, Enum):
    VALUE = "value"
    TYPE = "type"
    MISSING_LEFT = "missing_left"
    MISSING_RIGHT = "missing_right"
    LENGTH = "length"
    DUPLICATE_KEY = "duplicate_key"


@dataclass(frozen=True)
class Tolerance:
    abs_tol: float = 0.0
    rel_tol: float = 0.0

    def accepts(self, left: float, right: float) -> bool:
        return math.isclose(left, right, rel_tol=self.rel_tol, abs_tol=self.abs_tol)

    def __str__(self) -> str:
        return f"tolerance(abs={self.abs_tol:g}, rel={self.rel_tol:g})"


@dataclass(frozen=True)
class Difference:
    path: str
    kind: DiffKind
    left: Any
    right: Any
    rule: str

    def __str__(self) -> str:
        return f"{self.path}: {self.left!r} != {self.right!r} [{self.kind.value}; {self.rule}]"


@dataclass
class EquivalenceReport:
    differences: list[Difference] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)
    compared: int = 0

    @property
    def equivalent(self) -> bool:
        return not self.differences

    def __bool__(self) -> bool:
        return self.equivalent

    def render(self, left_label: str = "left", right_label: str = "right") -> str:
        head = (
            f"{len(self.differences)} difference(s); "
            f"{self.compared} leaf value(s) compared, {len(self.ignored)} ignored"
        )
        if self.equivalent:
            return f"equivalent: {head}"
        rows = [("path", left_label, right_label, "kind", "rule")]
        rows += [
            (d.path, _short(d.left), _short(d.right), d.kind.value, d.rule)
            for d in self.differences
        ]
        widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
        lines = ["  ".join(c.ljust(w) for c, w in zip(r, widths, strict=True)) for r in rows]
        lines.insert(1, "  ".join("-" * w for w in widths))
        return f"not equivalent: {head}\n" + "\n".join(line.rstrip() for line in lines)


class EquivalenceOracle:
    """Compare two nested results under path-scoped rules."""

    def __init__(
        self,
        ignore: Iterable[str] = (),
        tolerances: Mapping[str, Tolerance] | None = None,
        date_shifts: Mapping[str, Shift] | None = None,
        keyed: Mapping[str, Callable[[Any], Hashable]] | None = None,
    ) -> None:
        self.ignore = tuple(ignore)
        self.tolerances = dict(tolerances or {})
        self.date_shifts = dict(date_shifts or {})
        self.keyed = dict(keyed or {})
        for pattern in (*self.ignore, *self.tolerances, *self.date_shifts, *self.keyed):
            _paths.parse(pattern)  # fail at construction on a malformed pattern

    def compare(self, left: Any, right: Any) -> EquivalenceReport:
        report = EquivalenceReport()
        self._walk(left, right, (), report)
        return report

    def _walk(self, left: Any, right: Any, path: Path, report: EquivalenceReport) -> None:
        if path and _paths.matches_any(self.ignore, path):
            report.ignored.append(_paths.render(path))
            return
        if isinstance(left, Mapping) and isinstance(right, Mapping):
            for key in sorted({*left, *right}, key=str):
                child = (*path, key)
                if key not in right:
                    self._missing(child, left[key], DiffKind.MISSING_RIGHT, report)
                elif key not in left:
                    self._missing(child, right[key], DiffKind.MISSING_LEFT, report)
                else:
                    self._walk(left[key], right[key], child, report)
            return
        if _is_list(left) and _is_list(right):
            key_pattern = _paths.matches_any(self.keyed, path)
            if key_pattern is not None:
                self._walk_keyed(left, right, path, self.keyed[key_pattern], report)
                return
            if len(left) != len(right):
                report.differences.append(
                    Difference(_paths.render(path), DiffKind.LENGTH, len(left), len(right), "exact")
                )
            for index, (lval, rval) in enumerate(zip(left, right, strict=False)):
                self._walk(lval, rval, (*path, index), report)
            return
        report.compared += 1
        verdict = self._leaf(left, right, path)
        if verdict is not None:
            report.differences.append(verdict)

    def _walk_keyed(
        self,
        left: Sequence[Any],
        right: Sequence[Any],
        path: Path,
        key_fn: Callable[[Any], Hashable],
        report: EquivalenceReport,
    ) -> None:
        sides = (_group(left, key_fn), _group(right, key_fn))
        keys = list(dict.fromkeys([*sides[0], *sides[1]]))
        for key in keys:
            child = (*path, _key_segment(key))
            lefts, rights = sides[0].get(key, []), sides[1].get(key, [])
            if len(lefts) > 1 or len(rights) > 1:
                report.differences.append(
                    Difference(
                        _paths.render(child),
                        DiffKind.DUPLICATE_KEY,
                        len(lefts),
                        len(rights),
                        "unique key (occurrences per side)",
                    )
                )
            elif not rights:
                self._missing(child, lefts[0], DiffKind.MISSING_RIGHT, report)
            elif not lefts:
                self._missing(child, rights[0], DiffKind.MISSING_LEFT, report)
            else:
                self._walk(lefts[0], rights[0], child, report)

    def _missing(self, path: Path, value: Any, kind: DiffKind, report: EquivalenceReport) -> None:
        if _paths.matches_any(self.ignore, path):
            report.ignored.append(_paths.render(path))
            return
        left, right = (value, ABSENT) if kind is DiffKind.MISSING_RIGHT else (ABSENT, value)
        report.differences.append(Difference(_paths.render(path), kind, left, right, "presence"))

    def _leaf(self, left: Any, right: Any, path: Path) -> Difference | None:
        where = _paths.render(path)
        shift_pattern = _paths.matches_any(self.date_shifts, path)
        if shift_pattern is not None:
            return self._compare_dates(where, left, right, self.date_shifts[shift_pattern])

        tol_pattern = _paths.matches_any(self.tolerances, path)
        if tol_pattern is not None and _is_number(left) and _is_number(right):
            tol = self.tolerances[tol_pattern]
            ok = tol.accepts(float(left), float(right))
            return None if ok else Difference(where, DiffKind.VALUE, left, right, str(tol))

        if _kind(left) != _kind(right):
            return Difference(where, DiffKind.TYPE, left, right, "same type")
        if left != right:
            return Difference(where, DiffKind.VALUE, left, right, "exact")
        return None

    @staticmethod
    def _compare_dates(where: str, left: Any, right: Any, shift: Shift) -> Difference | None:
        rule = f"date shift {shift}"
        lval, rval = as_temporal(left), as_temporal(right)
        if lval is None or rval is None:
            return Difference(where, DiffKind.TYPE, left, right, f"{rule} (not a date)")
        expected = shift.apply(lval)
        if _same_instant(expected, rval):
            return None
        return Difference(where, DiffKind.VALUE, left, right, f"{rule}: expected {expected}")


def assert_equivalent(
    left: Any,
    right: Any,
    oracle: EquivalenceOracle | None = None,
    *,
    left_label: str = "left",
    right_label: str = "right",
) -> EquivalenceReport:
    """Pytest helper: raise ``AssertionError`` with the rendered diff table."""
    report = (oracle or EquivalenceOracle()).compare(left, right)
    if not report.equivalent:
        raise AssertionError(report.render(left_label, right_label))
    return report


def _group(items: Sequence[Any], key_fn: Callable[[Any], Hashable]) -> dict[Hashable, list[Any]]:
    grouped: dict[Hashable, list[Any]] = {}
    for item in items:
        grouped.setdefault(key_fn(item), []).append(item)
    return grouped


def _key_segment(key: Hashable) -> str:
    parts = key if isinstance(key, tuple) else (key,)
    return "<" + "|".join(str(p) for p in parts) + ">"


def _is_list(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, str | bytes)


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _kind(value: Any) -> str:
    # bool is an int subclass and True == 1; treat them as different kinds.
    if isinstance(value, bool):
        return "bool"
    if _is_number(value):
        return "number"
    return type(value).__name__


def _same_instant(expected: date, actual: date) -> bool:
    try:
        return expected == actual
    except TypeError:  # naive vs aware datetime
        return False


def _short(value: Any, limit: int = 40) -> str:
    text = repr(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."
