"""Observed shape of an entity: what a test actually saw, never invented figures.

A test that acts on reused or seeded data needs to know the entity's shape
(its billing term, whether anything is booked, how many lines it has) and
must not fill a gap with a plausible default. :class:`ObservedShape` reads
named attributes off JSON the test already holds — no extra HTTP — and keeps
``None`` for anything not observed. ``describe()`` gives the one-line form a
log or failure message needs; ``require()`` turns "not observed" into a
precondition failure rather than a wrong verdict.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from api.engine.context_memory import resolve_path

Extractor = str | Callable[[Any], Any]


class ShapeNotObserved(AssertionError):
    """A required attribute was not observed on the entity."""


@dataclass(frozen=True)
class ObservedShape:
    label: str
    values: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def observe(cls, label: str, body: Any, extractors: Mapping[str, Extractor]) -> ObservedShape:
        """Read each attribute by dotted path or callable; anything unreadable stays None."""
        values: dict[str, Any] = {}
        for name, how in extractors.items():
            try:
                value = how(body) if callable(how) else resolve_path(body, how)
            except (KeyError, IndexError, TypeError, ValueError, AttributeError):
                value = None
            values[name] = None if value == "" else value
        return cls(label, values)

    @classmethod
    def unknown(cls, label: str = "unknown") -> ObservedShape:
        return cls(label, {})

    def get(self, name: str) -> Any:
        return self.values.get(name)

    def observed(self, name: str) -> bool:
        return self.values.get(name) is not None

    def require(self, *names: str) -> ObservedShape:
        missing = [n for n in names if not self.observed(n)]
        if missing:
            raise ShapeNotObserved(f"{self.label}: not observed: {missing} ({self.describe()})")
        return self

    def describe(self) -> str:
        parts = [f"{k}={'?' if v is None else v}" for k, v in self.values.items()]
        return f"{self.label} [{', '.join(parts) or 'nothing observed'}]"
