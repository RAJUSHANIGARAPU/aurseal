"""Completeness rules over an outbound message: each rule checks one thing and says why it exists.

A partner message can be well-formed and still be wrong for the receiver: a total that does
not equal its parts, a required group missing, a code the receiver does not know, an
embedded XML document that does not parse. Each :class:`Rule` checks one such property of a
*subject* and carries its own ``reason`` (who needs it and why), so a failure reads as a
finding, not a bare mismatch. :func:`evaluate` runs them all and reports every problem at once.

The subject is whatever the caller parsed: typically a sequence of
:class:`oracles.edifact.Segment` (or an object with a ``segments`` attribute), plus anything
else a rule extracts itself, such as an XML part. The factories build the common rules:

* :func:`totals_balance` — a total equals the sum of its parts;
* :func:`required_group` — at least ``n`` segments with a tag;
* :func:`code_mapped` — a value in the message is the table's code for a known input;
* :func:`declared_count` — a trailer's declared segment count matches;
* :func:`xml_well_formed` — an embedded XML part parses.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from oracles.edifact import Segment

Check = Callable[[Any], list[str]]
Value = Callable[[Any], "str | None"]


@dataclass(frozen=True)
class Rule:
    name: str
    reason: str
    check: Check


@dataclass(frozen=True)
class RuleResult:
    rule: str
    reason: str
    problems: tuple[str, ...]

    @property
    def holds(self) -> bool:
        return not self.problems


def evaluate(subject: Any, rules: Iterable[Rule]) -> list[RuleResult]:
    """Run every rule; a rule that crashes is reported as a problem, never skipped."""
    results = []
    for rule in rules:
        try:
            problems = tuple(rule.check(subject))
        except Exception as exc:  # noqa: BLE001 - a broken rule is a finding, not a pass
            problems = (f"rule could not be evaluated: {type(exc).__name__}: {exc}",)
        results.append(RuleResult(rule.name, rule.reason, problems))
    return results


def violations(results: Iterable[RuleResult]) -> list[str]:
    """One line per problem: ``rule: problem (reason)``. Empty when every rule holds."""
    return [f"{r.rule}: {p} ({r.reason})" for r in results for p in r.problems]


# -- reading values -----------------------------------------------------------------------------


def segments_of(subject: Any) -> Sequence[Segment]:
    return subject.segments if hasattr(subject, "segments") else subject


def segment_value(tag: str, element: int, *, qualifier: str | None = None) -> Value:
    """Element ``element`` of the first ``tag`` segment (whose element 0 is ``qualifier``)."""

    def read(subject: Any) -> str | None:
        for seg in segments_of(subject):
            if seg.tag != tag:
                continue
            if qualifier is not None and seg.element(0) != qualifier:
                continue
            return seg.element(element) or None
        return None

    read.__name__ = f"{tag}{'+' + qualifier if qualifier else ''}[{element}]"
    return read


def _amount(value: Value, subject: Any) -> Decimal | None:
    raw = value(subject)
    if raw is None:
        return None
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


# -- rule factories -----------------------------------------------------------------------------


def totals_balance(
    name: str,
    total: Value,
    parts: Sequence[Value],
    *,
    reason: str,
    tolerance: Decimal = Decimal("0.00"),
) -> Rule:
    def check(subject: Any) -> list[str]:
        values = [_amount(p, subject) for p in parts]
        stated = _amount(total, subject)
        labels = [getattr(p, "__name__", "part") for p in parts]
        missing = [lbl for lbl, v in zip(labels, values, strict=True) if v is None]
        if stated is None:
            missing.insert(0, getattr(total, "__name__", "total"))
        if missing:
            return [f"missing or non-numeric: {missing}"]
        summed = sum(values, Decimal(0))  # type: ignore[arg-type]
        if abs(summed - stated) > tolerance:  # type: ignore[operator]
            return [f"{' + '.join(labels)} = {summed} but total is {stated}"]
        return []

    return Rule(name, reason, check)


def required_group(name: str, tag: str, *, reason: str, at_least: int = 1) -> Rule:
    def check(subject: Any) -> list[str]:
        found = sum(1 for s in segments_of(subject) if s.tag == tag)
        return [] if found >= at_least else [f"{found} {tag} segment(s), need {at_least}"]

    return Rule(name, reason, check)


def code_mapped(
    name: str, value: Value, source: str, table: Mapping[str, str], *, reason: str
) -> Rule:
    """The message carries ``table[source]`` where ``value`` reads (``source``: the known input)."""

    def check(subject: Any) -> list[str]:
        if source not in table:
            return [f"no code mapped for {source!r} (known: {sorted(table)})"]
        found = value(subject)
        expected = table[source]
        return [] if found == expected else [f"{source!r} sent as {found!r}, expected {expected!r}"]

    return Rule(name, reason, check)


def declared_count(name: str, trailer: str, *, reason: str, element: int = 0) -> Rule:
    """The trailer segment declares the number of segments, itself included."""

    def check(subject: Any) -> list[str]:
        segs = list(segments_of(subject))
        last = segs[-1] if segs else None
        if last is None or last.tag != trailer:
            return [f"message does not end with {trailer}"]
        declared = last.element(element)
        return [] if declared == str(len(segs)) else [f"declares {declared}, has {len(segs)}"]

    return Rule(name, reason, check)


def xml_well_formed(
    name: str,
    extract: Callable[[Any], str | bytes | None],
    *,
    reason: str,
    allow_declaration: bool = True,
) -> Rule:
    def check(subject: Any) -> list[str]:
        text = extract(subject)
        if text is None or not str(text).strip():
            return ["XML part is absent or empty"]
        raw = text.decode() if isinstance(text, bytes) else text
        problems = []
        if not allow_declaration and raw.lstrip().startswith("<?xml"):
            problems.append("XML part carries a declaration the receiver does not accept")
        try:
            ET.fromstring(raw)
        except ET.ParseError as exc:
            problems.append(f"XML part is not well-formed: {exc}")
        return problems

    return Rule(name, reason, check)
