"""Settle read: after an asynchronous write, read until two consecutive reads agree.

A predicate wait (:mod:`core.wait_utils`, ``wait_for_files``) answers "has X appeared?".
After a write that the server finishes in the background the question is different:
"has it stopped changing?". Judging the first read that looks plausible judges a
half-written state; judging after a fixed sleep is either slow or wrong.

:func:`settle` reads once, then keeps sleeping and reading until two consecutive reads
are equal, and returns the second. When the bound passes first it still returns the last
read, with ``stable=False`` and a warning, so the caller decides whether a still-changing
value may be judged (usually: abstain, or say so in the failure)::

    result = settle(lambda: client.get_subscription(number), bound_s=20, interval_s=1,
                    normalise=drop_keys("updated_at", "etag"))
    if not result.stable:
        verdict.no_verdict(f"still changing after {result.reads} reads")

``normalise`` maps a read to what is compared, so volatile fields (a timestamp the
server bumps on every read, a request id) do not keep the value "changing" forever. The
returned ``value`` is the raw last read, not the normalised one.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from core.logger import logger

T = TypeVar("T")


@dataclass(frozen=True)
class Settled(Generic[T]):
    value: T
    stable: bool
    reads: int


def drop_keys(*keys: str) -> Callable[[Any], Any]:
    """A ``normalise`` that removes ``keys`` from a mapping at every depth."""
    dropped = frozenset(keys)

    def strip(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {k: strip(v) for k, v in value.items() if k not in dropped}
        if isinstance(value, list | tuple):
            return [strip(v) for v in value]
        return value

    return strip


def settle(
    read: Callable[[], T],
    *,
    bound_s: float,
    interval_s: float,
    normalise: Callable[[T], Any] | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> Settled[T]:
    """Read until two consecutive reads compare equal, at most for ``bound_s`` seconds."""
    if interval_s <= 0 or bound_s < 0:
        raise ValueError("settle needs interval_s > 0 and bound_s >= 0")
    compare = normalise or (lambda value: value)
    deadline = clock() + bound_s
    value = read()
    reads = 1
    previous = compare(value)
    while clock() < deadline:
        sleep(interval_s)
        value = read()
        reads += 1
        current = compare(value)
        if current == previous:
            return Settled(value, True, reads)
        previous = current
    logger.warning(
        "[settle] still changing after %d read(s) in %.1fs; judging the last read as unsettled",
        reads,
        bound_s,
    )
    return Settled(value, False, reads)


__all__ = ["Settled", "drop_keys", "settle"]
