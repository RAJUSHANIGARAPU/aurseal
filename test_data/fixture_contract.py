"""Offline contract checks over seed fixtures: catch a bad fixture before it reaches an environment.

A fixture that violates a server-side rule does not fail where it is written;
it fails mid-seed on a live run as a 4xx, often reported as an abstention
rather than a failure. These rules run in the offline suite instead:

* :class:`Required` — a field must be present and non-empty;
* :class:`When` — if one field has a value, another must satisfy a predicate
  (e.g. "postal delivery needs at least one postal address");
* :class:`AtMost` — at most N array elements match a predicate (e.g. one
  primary address);
* :class:`CarriesRunTag` — a field contains a placeholder such as
  ``${SEED_TAG}``, so a parallel run never creates a same-named record;
* :func:`unique_across` — a field's value is unique across a set of fixtures.

Paths are dotted; ``[]`` fans out over an array: ``addresses[].purpose``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

_ABSENT = object()


def values_at(doc: Any, path: str) -> list[Any]:
    """Every value at ``path``; ``[]`` fans out over arrays. Missing branches contribute nothing."""
    current = [doc]
    for part in path.split("."):
        fan = part.endswith("[]")
        key = part[:-2] if fan else part
        nxt = []
        for node in current:
            value = node.get(key, _ABSENT) if isinstance(node, Mapping) else _ABSENT
            if value is _ABSENT:
                continue
            if fan:
                nxt.extend(value if isinstance(value, list) else [])
            else:
                nxt.append(value)
        current = nxt
    return current


def _filled(value: Any) -> bool:
    return value not in (None, "", [], {})


@dataclass(frozen=True)
class Violation:
    fixture: str
    rule: str
    detail: str

    def __str__(self) -> str:
        return f"{self.fixture}: [{self.rule}] {self.detail}"


class Rule:
    name = "rule"

    def check(self, fixture: str, doc: Any) -> list[Violation]:  # pragma: no cover - interface
        raise NotImplementedError


@dataclass(frozen=True)
class Required(Rule):
    path: str
    name: str = "required"

    def check(self, fixture: str, doc: Any) -> list[Violation]:
        found = values_at(doc, self.path)
        if not found or not all(_filled(v) for v in found):
            return [Violation(fixture, self.name, f"{self.path} is missing or empty")]
        return []


@dataclass(frozen=True)
class When(Rule):
    path: str
    equals: Any
    then_path: str
    satisfies: Callable[[list[Any]], bool]
    explain: str
    name: str = "conditional"

    def check(self, fixture: str, doc: Any) -> list[Violation]:
        if self.equals not in values_at(doc, self.path):
            return []
        if self.satisfies(values_at(doc, self.then_path)):
            return []
        return [Violation(fixture, self.name, f"{self.path}={self.equals!r} but {self.explain}")]


@dataclass(frozen=True)
class AtMost(Rule):
    path: str
    limit: int
    matching: Callable[[Any], bool]
    name: str = "at-most"

    def check(self, fixture: str, doc: Any) -> list[Violation]:
        count = sum(1 for v in values_at(doc, self.path) if self.matching(v))
        if count > self.limit:
            return [
                Violation(fixture, self.name, f"{count} entries at {self.path}, max {self.limit}")
            ]
        return []


@dataclass(frozen=True)
class CarriesRunTag(Rule):
    path: str
    tag: str = "${SEED_TAG}"
    name: str = "run-tag"

    def check(self, fixture: str, doc: Any) -> list[Violation]:
        found = values_at(doc, self.path)
        if not found or not all(isinstance(v, str) and self.tag in v for v in found):
            return [Violation(fixture, self.name, f"{self.path} does not carry {self.tag}")]
        return []


def check(fixtures: Mapping[str, Any], rules: Iterable[Rule]) -> list[Violation]:
    """Run every rule over every fixture document."""
    rules = list(rules)
    return [v for name, doc in fixtures.items() for rule in rules for v in rule.check(name, doc)]


def unique_across(fixtures: Mapping[str, Any], path: str) -> list[Violation]:
    """Each value at ``path`` appears in at most one fixture."""
    owners: dict[str, list[str]] = {}
    for name, doc in fixtures.items():
        for value in values_at(doc, path):
            owners.setdefault(repr(value), []).append(name)
    return [
        Violation(", ".join(names), "unique", f"{path}={value} is shared")
        for value, names in owners.items()
        if len(names) > 1
    ]
