"""Tests for oracles.time_window."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from oracles.time_window import TimestampError, parse_instant, produced_since

DEPLOY = datetime(2031, 5, 4, 10, 0, tzinfo=timezone.utc)

RECORDS = [
    {"id": 1, "type": "statement", "created_at": "2031-05-04T11:59:59+02:00"},  # 09:59:59Z
    {"id": 2, "type": "statement", "created_at": "2031-05-04T12:00:00+02:00"},  # 10:00Z
    {"id": 3, "type": "statement", "created_at": "2031-05-04T10:30:00Z"},
    {"id": 4, "type": "notice", "created_at": "2031-05-04T11:00:00Z"},
]


def test_selects_by_type_and_instant_across_offsets():
    assert [r["id"] for r in produced_since(RECORDS, "statement", DEPLOY)] == [2, 3]
    assert [r["id"] for r in produced_since(RECORDS, None, DEPLOY)] == [2, 3, 4]


def test_custom_keys():
    rows = [{"kind": "x", "at": "2031-05-04T10:00:00.5Z"}]
    assert produced_since(rows, "x", DEPLOY, type_key="kind", time_key="at") == rows


def test_naive_cutoff_is_an_error():
    with pytest.raises(TimestampError, match="naive"):
        produced_since(RECORDS, "statement", datetime(2031, 5, 4, 10, 0))


@pytest.mark.parametrize("stamp", ["2031-05-04T10:00:00", "yesterday", None, ""])
def test_unjudgeable_record_of_the_wanted_type_is_an_error(stamp):
    with pytest.raises(TimestampError, match="in record"):
        produced_since([{"type": "statement", "created_at": stamp}], "statement", DEPLOY)


def test_other_types_are_not_parsed():
    rows = [{"type": "notice", "created_at": "garbage"}]
    assert produced_since(rows, "statement", DEPLOY) == []


def test_parse_instant():
    assert parse_instant("2031-05-04T12:00:00+02:00") == DEPLOY
    assert parse_instant("2031-05-04T10:00:00z") == DEPLOY
    assert parse_instant("2031-05-04T10:00:00Z") - DEPLOY == timedelta(0)
