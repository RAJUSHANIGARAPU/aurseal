"""Tests for verification.idempotency.

The racy fakes force the interleaving (a barrier or an ``await`` between the
check and the write) so the concurrent failure is deterministic, not lucky.
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from verification.idempotency import (
    IdempotencyReport,
    Mode,
    assert_exactly_once,
    probe,
    probe_async,
)


class DuplicateRequest(Exception):
    pass


class RecordStore:
    def __init__(self, *, keyed: bool, locked: bool, race_width: int = 0) -> None:
        self.rows: list[str] = []
        self.keyed, self.locked = keyed, locked
        self._lock = threading.Lock()
        # Holds every racing caller between check and write, forcing the race.
        self._gap = threading.Barrier(race_width, timeout=5) if race_width else None

    def submit(self, key: str = "req-1") -> str:
        if self.locked:
            with self._lock:
                return self._check_then_write(key)
        return self._check_then_write(key)

    def _check_then_write(self, key: str) -> str:
        exists = self.keyed and key in self.rows
        if self._gap is not None:
            self._gap.wait()
        if exists:
            raise DuplicateRequest(key)
        self.rows.append(key)
        return key


def test_keyed_store_is_idempotent_sequentially():
    store = RecordStore(keyed=True, locked=False)
    report = probe(store.submit, lambda: len(store.rows))
    assert report.exactly_once, report.render()
    assert len(report.errors) == 1  # duplicate rejected, which is allowed


def test_unkeyed_store_double_writes_sequentially():
    store = RecordStore(keyed=False, locked=False)
    report = probe(store.submit, lambda: len(store.rows), attempts=3)
    assert report.effect_delta == 3
    with pytest.raises(AssertionError, match="effect applied 3x, expected 1x"):
        assert_exactly_once(report)


def test_check_then_act_race_only_shows_up_concurrently():
    store = RecordStore(keyed=True, locked=False, race_width=2)
    report = probe(store.submit, lambda: len(store.rows), mode=Mode.THREADS)
    assert report.effect_delta == 2, report.render()
    assert not report.exactly_once


def test_locked_store_survives_concurrent_double_submit():
    store = RecordStore(keyed=True, locked=True)
    report = probe(store.submit, lambda: len(store.rows), mode="threads", attempts=8)
    assert_exactly_once(report)


def test_rejection_can_be_disallowed():
    store = RecordStore(keyed=True, locked=False)
    report = probe(store.submit, lambda: len(store.rows), allow_rejection=False)
    assert "raised: DuplicateRequest" in report.render()


def test_no_successful_attempt_is_a_problem():
    def always_fails() -> None:
        raise RuntimeError("down")

    report = probe(always_fails, lambda: 0)
    assert "no attempt succeeded" in report.problems


def test_probe_needs_two_attempts():
    with pytest.raises(ValueError):
        probe(lambda: None, lambda: 0, attempts=1)


class AsyncStore:
    def __init__(self, *, locked: bool) -> None:
        self.rows: list[str] = []
        self._lock = asyncio.Lock() if locked else None

    async def submit(self) -> str:
        if self._lock is None:
            return await self._check_then_write()
        async with self._lock:
            return await self._check_then_write()

    async def _check_then_write(self) -> str:
        exists = "req-1" in self.rows
        await asyncio.sleep(0)  # yield between check and write
        if exists:
            raise DuplicateRequest("req-1")
        self.rows.append("req-1")
        return "req-1"


def _run_async(locked: bool) -> IdempotencyReport:
    store = AsyncStore(locked=locked)
    return asyncio.run(probe_async(store.submit, lambda: len(store.rows), attempts=3))


def test_async_race_is_detected():
    report = _run_async(locked=False)
    assert report.mode is Mode.ASYNCIO
    assert report.effect_delta == 3 and not report.exactly_once


def test_async_locked_store_is_exactly_once():
    assert_exactly_once(_run_async(locked=True))
