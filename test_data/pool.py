"""Reuse pool: find existing entities fit for a test, fall back to creating one.

Building an entity per test is the safe default but can be slow; a pool of
existing entities in the right shape is fast but only safe when the shape is
checked, not assumed. :class:`ReusePool` lists candidates, keeps the ones the
caller's ``fit`` predicate accepts, orders them by ``preference`` and returns
at most ``limit``.

:func:`select_targets` is the one decision every data-dependent test makes,
driven by :mod:`test_data.seed_mode`:

* ``fresh``   — seed one; the pool is never listed.
* ``reuse``   — pool first (restricted to the allowlist when one is set),
  seed as fallback when the pool is dry.
* ``targets`` — allowlisted pool entries only; seeding is refused and an empty
  result raises :class:`NoTarget` so the caller can skip with that reason.

:func:`replenish` keeps an allowlist stocked: it seeds N entities through a
caller-supplied seeder and writes them as a targets file for a later
``targets`` run.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Generic, TypeVar

from test_data.seed_mode import SeedConfig, SeedMode

logger = logging.getLogger(__name__)

T = TypeVar("T")


class NoTarget(LookupError):
    """No entity is available under the current seed mode; the reason says why."""


@dataclass(frozen=True)
class Target(Generic[T]):
    """One entity a test may act on, and whether this run created it."""

    entity: T
    label: str
    was_seeded: bool


class ReusePool(Generic[T]):
    def __init__(
        self,
        list_candidates: Callable[[], Iterable[T]],
        *,
        fit: Callable[[T], bool],
        label: Callable[[T], str] = str,
        preference: Callable[[T], Any] | None = None,
    ) -> None:
        self._list = list_candidates
        self._fit = fit
        self._label = label
        self._preference = preference

    def find(self, limit: int, *, allowed: Callable[[str], bool] | None = None) -> list[T]:
        """Up to ``limit`` fitting candidates, preferred first (stable for equal preference)."""
        fitting = []
        rejected = 0
        for candidate in self._list():
            if allowed is not None and not allowed(self._label(candidate)):
                continue
            if self._fit(candidate):
                fitting.append(candidate)
            else:
                rejected += 1
        if self._preference is not None:
            fitting.sort(key=self._preference)
        logger.info("reuse pool: %d fit, %d rejected by shape", len(fitting), rejected)
        return fitting[:limit]

    def label(self, entity: T) -> str:
        return self._label(entity)


def select_targets(
    config: SeedConfig,
    pool: ReusePool[T],
    seed: Callable[[], T] | None,
    *,
    limit: int = 1,
) -> list[Target[T]]:
    """The ordered entities a test should try, per the seed mode. Never empty: raises NoTarget."""
    if config.mode is SeedMode.FRESH:
        return [_seeded(pool, seed, "fresh mode builds its own entity")]

    allowed = (lambda label: label in config.allowlist) if config.allowlist else None
    found = [Target(e, pool.label(e), False) for e in pool.find(limit, allowed=allowed)]
    if found:
        return found
    if config.mode is SeedMode.TARGETS:
        raise NoTarget(
            f"no allowlisted entity ({config.allowlist.source}) has the shape this test needs; "
            "targets mode never builds one"
        )
    return [_seeded(pool, seed, "reuse pool is dry")]


def _seeded(pool: ReusePool[T], seed: Callable[[], T] | None, why: str) -> Target[T]:
    if seed is None:
        raise NoTarget(f"{why}, and this test has no seed path")
    entity = seed()
    logger.info("%s: seeded %s", why, pool.label(entity))
    return Target(entity, pool.label(entity), True)


# -- pool replenishment --------------------------------------------------------

REPLENISH_COUNT_KEY = "REPLENISH_COUNT"
REPLENISH_BOOKED_KEY = "REPLENISH_BOOKED"
DEFAULT_REPLENISH_COUNT = 5
DEFAULT_TARGETS_FILE = Path("reports") / "entity-allowlist" / "replenished.txt"


class ReplenishError(RuntimeError):
    """Replenishment was refused, or seeded nothing."""


@dataclass(frozen=True)
class ReplenishResult:
    """What a replenishment run built, what failed, and where the targets file is."""

    seeded: tuple[str, ...]
    failures: tuple[str, ...]
    path: Path
    booked: bool


def replenish(
    count: int,
    seed: Callable[[bool], T],
    *,
    booked: bool = False,
    label: Callable[[T], str] = str,
    path: Path | str = DEFAULT_TARGETS_FILE,
    config: SeedConfig | None = None,
) -> ReplenishResult:
    """Seed ``count`` entities and write their labels as a targets file.

    ``seed(booked)`` builds one active entity, or with ``booked=True`` one whose
    first period is already booked. The file is in the allowlist format of
    :class:`test_data.seed_mode.PinnedTargetList` (one id per line, ``#``
    comments), so a later run consumes it with ``SEED_MODE=targets
    ENTITY_ALLOWLIST_FILE=<path>``.

    A seed that fails is recorded and the next one is tried; the run fails
    only when nothing was seeded. A run in ``targets`` mode is refused: that
    mode promises no data is created.
    """
    if count < 1:
        raise ReplenishError(f"count must be at least 1, got {count}")
    if config is not None and not config.may_build:
        raise ReplenishError(
            f"seed mode '{config.mode.value}' never creates data; replenish under fresh or reuse"
        )
    seeded: list[str] = []
    failures: list[str] = []
    for n in range(1, count + 1):
        try:
            entity = seed(booked)
        except Exception as exc:  # noqa: BLE001 - one failed seed must not stop the batch
            failures.append(f"#{n}: {type(exc).__name__}: {exc}")
            logger.warning("replenish: seed %d of %d failed: %s", n, count, exc)
            continue
        seeded.append(label(entity))
        logger.info("replenish: seeded %s (%d of %d)", seeded[-1], n, count)
    if not seeded:
        raise ReplenishError(f"no entity seeded out of {count}: {'; '.join(failures)}")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    kind = "first period booked" if booked else "active"
    header = f"# {len(seeded)} {kind} entities seeded for SEED_MODE=targets\n"
    target.write_text(header + "\n".join(seeded) + "\n", encoding="utf-8")
    return ReplenishResult(tuple(seeded), tuple(failures), target, booked)


def replenish_settings(environ: Mapping[str, str] | None = None) -> tuple[int, bool]:
    """``(count, booked)`` from ``REPLENISH_COUNT`` (default 5) and ``REPLENISH_BOOKED``."""
    env = os.environ if environ is None else environ
    raw = env.get(REPLENISH_COUNT_KEY, "").strip()
    count = int(raw) if raw else DEFAULT_REPLENISH_COUNT
    booked = env.get(REPLENISH_BOOKED_KEY, "").strip().lower() == "true"
    return count, booked
