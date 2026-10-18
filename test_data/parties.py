"""Resolve a live entity by name — and refuse when the name does not identify exactly one.

Environments that are re-seeded on a cycle accumulate same-named records. A
resolver that returns "the first match" then binds the API path to one record
while the UI picker (with its own ordering) binds to another, and every symptom
surfaces far from the cause. :func:`select_unique` fails loudly on more than one
selectable match instead.

When the preferred name is absent (rotated away by a reseed), it falls back to
a current selectable entity — preferring names with ``fallback_prefix`` — and
logs that the hint is stale. An entity list with nothing selectable fails with
an "unseeded environment" message.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping
from typing import Any

logger = logging.getLogger(__name__)

Record = Mapping[str, Any]


class AmbiguousMatch(LookupError):
    """More than one selectable entity carries the requested name."""


class NothingSelectable(LookupError):
    """No selectable entity exists for this kind; the environment is not seeded for it."""


def select_unique(
    candidates: Iterable[Record],
    kind: str,
    preferred: str | None,
    *,
    name_of: Callable[[Record], str] = lambda r: str(r.get("name") or ""),
    id_of: Callable[[Record], str] = lambda r: str(r.get("id") or ""),
    selectable: Callable[[Record], bool] = lambda r: True,
    fallback_prefix: str | None = None,
) -> Record:
    """Pick the entity for ``kind``: the preferred name when it is unique, else a fallback."""
    pool = [c for c in candidates if selectable(c)]
    if preferred:
        matches = [c for c in pool if name_of(c) == preferred]
        if len(matches) > 1:
            raise AmbiguousMatch(
                f"'{preferred}' is not unique: {len(matches)} selectable {kind} records carry it "
                f"(ids {[id_of(m) for m in matches]}). Select by something that identifies exactly "
                "one record, or remove the stale duplicates."
            )
        if matches:
            return matches[0]
        logger.warning("%s '%s' not found; discovering a current one (stale hint)", kind, preferred)

    named = [c for c in pool if name_of(c).strip()]
    if fallback_prefix:
        for c in named:
            if name_of(c).startswith(fallback_prefix):
                return c
    if named:
        return named[0]
    raise NothingSelectable(f"no selectable {kind} on this environment — is it seeded?")


class LiveResolver:
    """Caches one resolution per (kind, preferred name) for the life of the resolver."""

    def __init__(self, list_kind: Callable[[str], Iterable[Record]], **select_kwargs: Any) -> None:
        self._list = list_kind
        self._kwargs = select_kwargs
        self._cache: dict[tuple[str, str | None], Record] = {}

    def resolve(self, kind: str, preferred: str | None = None) -> Record:
        key = (kind, preferred)
        if key not in self._cache:
            self._cache[key] = select_unique(self._list(kind), kind, preferred, **self._kwargs)
        return self._cache[key]
