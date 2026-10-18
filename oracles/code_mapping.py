"""Expected-code oracle for a data migration that rewrites codes by description.

A migration often re-codes records by a lookup table: every term whose
description is ``"Standard cover"`` must end up with code ``"PLN-110"``. To
tell a migrated record from one the migration skipped, a test compares each
term's actual code with the code the table expects.

The table is **read from a data file** (JSON object, description -> code),
never copied into test code: a hand-maintained copy drifts from the source the
migration uses, and the oracle then judges against stale expectations. Point
:meth:`CodeMapping.load` at the same file the migration reads, or at an export
of it.

Descriptions match after trimming, collapsing inner whitespace and case
folding, so a re-wrapped label still maps.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

_WS = re.compile(r"\s+")


def _key(description: str) -> str:
    return _WS.sub(" ", description.strip()).casefold()


class MigrationState(str, Enum):
    MIGRATED = "migrated"
    """Every mapped term carries its expected code."""
    UNMIGRATED = "unmigrated"
    """No mapped term carries its expected code."""
    PARTIAL = "partial"
    """Some mapped terms do, some do not."""
    NOT_APPLICABLE = "not-applicable"
    """No term has a description the table knows."""


@dataclass(frozen=True)
class CodeMatch:
    entity: str
    description: str
    actual: str | None
    expected: str

    @property
    def converted(self) -> bool:
        return self.actual == self.expected

    def __str__(self) -> str:
        mark = "ok" if self.converted else "NOT CONVERTED"
        return f"{self.entity}: {self.description!r} {self.actual!r} -> {self.expected!r} {mark}"


class CodeMapping:
    def __init__(self, table: Mapping[str, str], source: str = "<inline>") -> None:
        self.source = source
        self._table: dict[str, str] = {}
        for description, code in table.items():
            if not isinstance(description, str) or not isinstance(code, str) or not code:
                raise ValueError(f"{source}: entry {description!r}: {code!r} is not text -> code")
            key = _key(description)
            if key in self._table and self._table[key] != code:
                raise ValueError(
                    f"{source}: {description!r} maps to both {self._table[key]!r} and {code!r}"
                )
            self._table[key] = code

    @classmethod
    def load(cls, path: str | Path) -> CodeMapping:
        """Read a JSON object ``{description: code}``; an empty table is an error."""
        p = Path(path)
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not data:
            raise ValueError(f"{p}: expected a non-empty JSON object of description -> code")
        return cls(data, str(p))

    def __len__(self) -> int:
        return len(self._table)

    def expected_for(self, description: str | None) -> str | None:
        return self._table.get(_key(description)) if description else None

    def mapped_terms(
        self,
        entity: str,
        terms: Iterable[Mapping[str, Any]],
        *,
        description_key: str = "description",
        code_key: str = "code",
    ) -> list[CodeMatch]:
        """The terms the migration acts on, with actual and expected code, in input order."""
        mapped = []
        for term in terms:
            description = term.get(description_key)
            expected = self.expected_for(description) if isinstance(description, str) else None
            if expected is None:
                continue
            actual = term.get(code_key)
            mapped.append(
                CodeMatch(entity, description, None if actual is None else str(actual), expected)
            )
        return mapped

    def state(self, mapped: Iterable[CodeMatch]) -> MigrationState:
        terms = list(mapped)
        if not terms:
            return MigrationState.NOT_APPLICABLE
        done = sum(t.converted for t in terms)
        if done == len(terms):
            return MigrationState.MIGRATED
        return MigrationState.UNMIGRATED if done == 0 else MigrationState.PARTIAL
