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

Pinned reference data is different: a test that depends on one specific
record must not quietly bind to another. :func:`select_by_key` resolves by a
unique business key (a reference number, never a display name) with **no
fallback**: zero matches is :class:`KeyNotFound` ("refresh the pinned key"),
more than one is :class:`AmbiguousMatch` with the ids. :meth:`LiveResolver.resolve_key`
caches one answer per ``(kind, key)`` per run and can scan a paged listing
oldest first, stopping at the first page that carries the key.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping
from typing import Any

logger = logging.getLogger(__name__)

Record = Mapping[str, Any]


class AmbiguousMatch(LookupError):
    """More than one selectable entity carries the requested name."""


class KeyNotFound(LookupError):
    """No selectable record carries the pinned business key; the pin is stale."""


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


def _reference(record: Record) -> str:
    return str(record.get("reference") or "")


def select_by_key(
    candidates: Iterable[Record],
    kind: str,
    key: str,
    *,
    key_of: Callable[[Record], str] = _reference,
    selectable: Callable[[Record], bool] = lambda r: True,
    id_of: Callable[[Record], str] = lambda r: str(r.get("id") or ""),
) -> Record:
    """The one selectable ``kind`` record whose business key equals ``key``. No fallback."""
    if not key or not str(key).strip():
        raise ValueError(f"select_by_key needs a business key for {kind}, got {key!r}")
    matches = [c for c in candidates if selectable(c) and str(key_of(c)) == key]
    if not matches:
        raise KeyNotFound(
            f"no selectable {kind} carries key {key!r}: refresh the pinned key (the environment "
            "was re-seeded, or the record was retired). Not falling back to another record."
        )
    if len(matches) > 1:
        raise AmbiguousMatch(
            f"key {key!r} is not unique: {len(matches)} selectable {kind} records carry it "
            f"(ids {[id_of(m) for m in matches]})."
        )
    return matches[0]


def _carries(page: Iterable[Record], key: str, key_of: Callable[[Record], str]) -> list[Record]:
    rows = list(page)
    return rows if any(str(key_of(r)) == key for r in rows) else []


class LiveResolver:
    """Caches one resolution per (kind, preferred name) for the life of the resolver.

    ``list_pages`` (optional) yields a kind's listing one page at a time, oldest
    first; :meth:`resolve_key` uses it to stop reading at the first page that
    carries the key instead of loading the whole listing.
    """

    def __init__(
        self,
        list_kind: Callable[[str], Iterable[Record]],
        *,
        list_pages: Callable[[str], Iterable[Iterable[Record]]] | None = None,
        key_of: Callable[[Record], str] = _reference,
        **select_kwargs: Any,
    ) -> None:
        self._list = list_kind
        self._pages = list_pages
        self._key_of = key_of
        self._kwargs = select_kwargs
        self._cache: dict[tuple[str, str | None], Record] = {}
        self._key_cache: dict[tuple[str, str], Record] = {}

    def resolve(self, kind: str, preferred: str | None = None) -> Record:
        key = (kind, preferred)
        if key not in self._cache:
            self._cache[key] = select_unique(self._list(kind), kind, preferred, **self._kwargs)
        return self._cache[key]

    def resolve_key(self, kind: str, key: str) -> Record:
        """The record for a pinned business key, cached per ``(kind, key)``; never a fallback."""
        cache_key = (kind, key)
        if cache_key not in self._key_cache:
            selectable = self._kwargs.get("selectable", lambda r: True)
            id_of = self._kwargs.get("id_of", lambda r: str(r.get("id") or ""))
            if self._pages is not None:
                candidates: list[Record] = []
                for page in self._pages(kind):
                    candidates = _carries(page, key, self._key_of)
                    if candidates:
                        break
            else:
                candidates = list(self._list(kind))
            self._key_cache[cache_key] = select_by_key(
                candidates, kind, key, key_of=self._key_of, selectable=selectable, id_of=id_of
            )
        return self._key_cache[cache_key]
