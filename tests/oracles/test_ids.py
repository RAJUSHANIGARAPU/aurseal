"""Tests for oracles.ids (UUID version and v7 timestamp checks)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from oracles.ids import (
    consistent_with_generation_time,
    time_order_violations,
    uuid7_timestamp,
    uuid_version,
)

UTC = timezone.utc


def make_v7(moment: datetime, rand: int = 0x123) -> str:
    millis = int(moment.timestamp() * 1000)
    value = (millis << 80) | (0x7 << 76) | (rand << 64) | (0b10 << 62) | 0xABCDEF
    return str(uuid.UUID(int=value))


def test_versions():
    assert uuid_version(str(uuid.uuid4())) == 4
    assert uuid_version(make_v7(datetime(2031, 5, 4, tzinfo=UTC))) == 7
    assert uuid_version(uuid.uuid1()) == 1
    with pytest.raises(ValueError, match="not a UUID"):
        uuid_version("ORD-42")
    with pytest.raises(ValueError, match="variant"):
        uuid_version("00000000-0000-4000-0000-000000000000")


def test_v7_timestamp():
    moment = datetime(2031, 5, 4, 10, 15, 30, 123000, tzinfo=UTC)
    assert uuid7_timestamp(make_v7(moment)) == moment
    with pytest.raises(ValueError, match="v4 UUID"):
        uuid7_timestamp(uuid.uuid4())


def test_consistency_with_claimed_generation_time():
    moment = datetime(2031, 5, 4, 10, 0, tzinfo=UTC)
    ident = make_v7(moment)
    local = moment.astimezone(timezone(timedelta(hours=2)))  # same instant, other offset
    assert consistent_with_generation_time(ident, local)
    assert consistent_with_generation_time(ident, moment + timedelta(seconds=4))
    assert not consistent_with_generation_time(ident, moment + timedelta(seconds=6))
    with pytest.raises(ValueError, match="naive"):
        consistent_with_generation_time(ident, datetime(2031, 5, 4, 10, 0))


def test_time_order():
    base = datetime(2031, 5, 4, tzinfo=UTC)
    ids = [make_v7(base), make_v7(base + timedelta(seconds=1)), make_v7(base)]
    [v] = time_order_violations(ids)
    assert v.startswith(ids[2]) and v.endswith(ids[1])
    assert time_order_violations(ids[:2]) == []
