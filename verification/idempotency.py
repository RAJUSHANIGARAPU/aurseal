"""Idempotency / double-submit probe.

Submits the same operation more than once, sequentially or concurrently, and
measures the effect through an independent read (``effect_count``), not the
operation's return value. Exactly-once means the count moved by exactly one.

A duplicate submit may legitimately be *rejected* (an error for the second
call); that is accepted by default, but at least one call must succeed. Pass
``allow_rejection=False`` when the contract is "every call returns success".

Concurrent probes release all callers from a barrier at the same moment, which
is what opens check-then-act races; a sequential probe alone never finds them.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Mode(str, Enum):
    SEQUENTIAL = "sequential"
    THREADS = "threads"
    ASYNCIO = "asyncio"


@dataclass
class IdempotencyReport:
    mode: Mode
    attempts: int
    effect_delta: int
    results: list[Any] = field(default_factory=list)
    errors: list[BaseException] = field(default_factory=list)
    allow_rejection: bool = True
    expected_delta: int = 1

    @property
    def problems(self) -> list[str]:
        out = []
        if self.effect_delta != self.expected_delta:
            out.append(f"effect applied {self.effect_delta}x, expected {self.expected_delta}x")
        if not self.results:
            out.append("no attempt succeeded")
        if self.errors and not self.allow_rejection:
            out.append(f"{len(self.errors)} attempt(s) raised: {self.errors[0]!r}")
        return out

    @property
    def exactly_once(self) -> bool:
        return not self.problems

    def render(self) -> str:
        head = (
            f"{self.mode.value} x{self.attempts}: {len(self.results)} ok, {len(self.errors)} raised"
        )
        if self.exactly_once:
            return f"exactly-once held ({head})"
        return f"exactly-once violated ({head}): " + "; ".join(self.problems)


def probe(
    operation: Callable[[], Any],
    effect_count: Callable[[], int],
    *,
    attempts: int = 2,
    mode: Mode | str = Mode.SEQUENTIAL,
    allow_rejection: bool = True,
    timeout: float = 10.0,
) -> IdempotencyReport:
    """Run a synchronous ``operation`` ``attempts`` times and measure the effect."""
    mode = Mode(mode)
    if mode is Mode.ASYNCIO:
        raise ValueError("use probe_async() for coroutine operations")
    if attempts < 2:
        raise ValueError("an idempotency probe needs at least two attempts")
    start = effect_count()
    outcomes: list[tuple[bool, Any]]
    if mode is Mode.SEQUENTIAL:
        outcomes = [_call(operation) for _ in range(attempts)]
    else:
        barrier = threading.Barrier(attempts, timeout=timeout)

        def released() -> tuple[bool, Any]:
            barrier.wait()
            return _call(operation)

        with ThreadPoolExecutor(max_workers=attempts) as pool:
            futures = [pool.submit(released) for _ in range(attempts)]
            outcomes = [f.result(timeout=timeout) for f in futures]
    return _report(mode, attempts, effect_count() - start, outcomes, allow_rejection)


async def probe_async(
    operation: Callable[[], Awaitable[Any]],
    effect_count: Callable[[], int | Awaitable[int]],
    *,
    attempts: int = 2,
    allow_rejection: bool = True,
) -> IdempotencyReport:
    """Run a coroutine ``operation`` concurrently via ``asyncio.gather``."""
    if attempts < 2:
        raise ValueError("an idempotency probe needs at least two attempts")
    start = await _maybe_await(effect_count())
    gathered = await asyncio.gather(*(operation() for _ in range(attempts)), return_exceptions=True)
    outcomes = [(not isinstance(r, BaseException), r) for r in gathered]
    delta = await _maybe_await(effect_count()) - start
    return _report(Mode.ASYNCIO, attempts, delta, outcomes, allow_rejection)


def assert_exactly_once(report: IdempotencyReport) -> None:
    if not report.exactly_once:
        raise AssertionError(report.render())


def _call(operation: Callable[[], Any]) -> tuple[bool, Any]:
    try:
        return True, operation()
    except Exception as exc:  # a rejected duplicate is a legitimate outcome
        return False, exc


async def _maybe_await(value: int | Awaitable[int]) -> int:
    return await value if isinstance(value, Awaitable) else value


def _report(
    mode: Mode,
    attempts: int,
    delta: int,
    outcomes: list[tuple[bool, Any]],
    allow_rejection: bool,
) -> IdempotencyReport:
    return IdempotencyReport(
        mode=mode,
        attempts=attempts,
        effect_delta=delta,
        results=[value for ok, value in outcomes if ok],
        errors=[value for ok, value in outcomes if not ok],
        allow_rejection=allow_rejection,
    )
