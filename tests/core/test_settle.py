"""Settle read: stabilises, never stabilises, normalise hides a volatile field."""

from __future__ import annotations

import pytest

from core.settle import drop_keys, settle


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def reads(*values):
    it = iter(values)
    last = [None]

    def read():
        try:
            last[0] = next(it)
        except StopIteration:
            pass
        return last[0]

    return read


def run(read, **kwargs):
    clock = FakeClock()
    kwargs.setdefault("bound_s", 10)
    kwargs.setdefault("interval_s", 1)
    return settle(read, clock=clock, sleep=clock.sleep, **kwargs)


def test_returns_on_the_first_equal_pair():
    result = run(reads({"seats": 1}, {"seats": 3}, {"seats": 5}, {"seats": 5}, {"seats": 9}))
    assert result.stable and result.value == {"seats": 5} and result.reads == 4


def test_never_stabilises_returns_the_last_read_flagged():
    counter = iter(range(100))
    result = run(lambda: next(counter), bound_s=3, interval_s=1)
    assert not result.stable
    assert result.value == 3 and result.reads == 4


def test_normalise_hides_a_volatile_timestamp_but_returns_the_raw_read():
    read = reads(
        {"status": "billing", "updated_at": "t1"},
        {"status": "active", "updated_at": "t2"},
        {"status": "active", "updated_at": "t3"},
    )
    result = run(read, normalise=drop_keys("updated_at"))
    assert result.stable and result.value == {"status": "active", "updated_at": "t3"}
    assert result.reads == 3


def test_without_normalise_the_timestamp_keeps_it_changing():
    stamps = iter(range(100))
    result = run(lambda: {"status": "active", "updated_at": next(stamps)}, bound_s=2)
    assert not result.stable


def test_drop_keys_works_at_depth():
    strip = drop_keys("etag")
    assert strip({"a": [{"etag": 1, "b": 2}], "etag": 3}) == {"a": [{"b": 2}]}


def test_zero_bound_is_a_single_unsettled_read():
    result = run(reads("x"), bound_s=0)
    assert not result.stable and result.reads == 1


def test_rejects_a_non_positive_interval():
    with pytest.raises(ValueError):
        run(reads(1), interval_s=0)
