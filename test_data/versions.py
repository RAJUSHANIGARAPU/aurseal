"""Find an asynchronously produced artefact on whichever version of a record it landed.

A write on a versioned record (a subscription changed today, a change scheduled for next
month) can leave several versions behind, and something the write produces in the
background (a document, a message, a ledger line) may attach to any of them: the current
one, or a later scheduled one. A test that only looks at "the latest" version reports a
missing artefact that is in fact sitting on the next one.

:func:`find_carrier` searches every reachable version, re-listing them each round because
the write may still be adding one, until a version carries the artefact or the wait is
over. Three answers, and the caller treats them differently:

* :class:`Carrier` — found; judge it;
* :class:`NotReadable` — not found, and at least one version could not be read yet (a
  future-dated version the service refuses to open). The artefact may be there: **abstain**;
* :class:`Absent` — every version was read and none carries it: **fail**, quoting
  ``searched`` so the reader sees which versions were looked at.

``read(version)`` raises :class:`VersionNotReadable` for a version that exists but cannot
be opened yet; any other exception propagates (a harness break is not an abstention)::

    found = find_carrier(lambda: client.versions(number), client.read_version,
                         lambda doc: any(d["kind"] == "invoice" for d in doc["documents"]),
                         wait_s=30, interval_s=2)
    if isinstance(found, NotReadable):
        verdict.no_verdict(found.reason)
    assert isinstance(found, Carrier), found.describe()
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from core.logger import logger


class VersionNotReadable(Exception):
    """Raised by ``read`` for a version that exists but cannot be read yet."""


@dataclass(frozen=True)
class Carrier:
    version: str
    document: Any
    reads: int

    def describe(self) -> str:
        return f"artefact found on version {self.version} after {self.reads} read(s)"


@dataclass(frozen=True)
class NotReadable:
    reason: str
    searched: tuple[str, ...]
    unreadable: tuple[str, ...]

    def describe(self) -> str:
        return self.reason


@dataclass(frozen=True)
class Absent:
    searched: tuple[str, ...]
    reads: int

    def describe(self) -> str:
        listed = ", ".join(self.searched) or "none (no version was listed)"
        return f"no version carries the artefact; searched {listed} in {self.reads} read(s)"


Found = Carrier | NotReadable | Absent


def find_carrier(
    list_versions: Callable[[], Iterable[str]],
    read: Callable[[str], Any],
    has_artefact: Callable[[Any], bool],
    *,
    wait_s: float,
    interval_s: float,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> Found:
    """Search every listed version, round after round, until one carries the artefact."""
    if interval_s <= 0 or wait_s < 0:
        raise ValueError("find_carrier needs interval_s > 0 and wait_s >= 0")
    deadline = clock() + wait_s
    searched: dict[str, None] = {}
    unreadable: dict[str, str] = {}
    reads = 0
    while True:
        for version in list_versions():
            version = str(version)
            searched.setdefault(version)
            reads += 1
            try:
                document = read(version)
            except VersionNotReadable as exc:
                unreadable[version] = str(exc) or "not readable yet"
                continue
            unreadable.pop(version, None)
            if has_artefact(document):
                found = Carrier(version, document, reads)
                logger.info("[versions] %s", found.describe())
                return found
        if clock() >= deadline:
            break
        sleep(interval_s)
    if unreadable:
        detail = "; ".join(f"{v}: {why}" for v, why in unreadable.items())
        return NotReadable(
            f"artefact not found on a readable version, and {len(unreadable)} version(s) "
            f"could not be read yet ({detail}); it may be there",
            tuple(searched),
            tuple(unreadable),
        )
    return Absent(tuple(searched), reads)


__all__ = ["Absent", "Carrier", "Found", "NotReadable", "VersionNotReadable", "find_carrier"]
