"""Tests for oracles.job_registration."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from oracles.job_registration import (
    Rule,
    StatusVocabulary,
    classify_registration,
    expected_run_time,
    first_pending,
    one_at_a_time_violations,
    parse_rows,
    parse_start,
    silently_stalled,
)


def rows(*spec):
    """spec: (status, month, job_id)"""
    return parse_rows(
        {
            "id": f"r{i}",
            "code": "INV",
            "status": status,
            "start": f"2031-{month:02d}-01T00:00:00+02:00",
            "job_id": job,
        }
        for i, (status, month, job) in enumerate(spec)
    )


class TestClassify:
    def test_one_at_a_time(self):
        assert (
            classify_registration(
                rows(("booked", 1, None), ("scheduled", 2, "j1"), ("pending", 3, None))
            )
            is Rule.SINGLE_SCHEDULED
        )

    def test_all_registered(self):
        assert classify_registration(rows(("scheduled", 1, "a"), ("scheduled", 2, "b"))) is (
            Rule.EVERY_ROW_SCHEDULED
        )

    def test_none_and_mixed_and_undetermined(self):
        assert classify_registration(rows(("pending", 1, None), ("pending", 2, None))) is (
            Rule.NOTHING_SCHEDULED
        )
        assert (
            classify_registration(
                rows(("scheduled", 1, "a"), ("scheduled", 2, "b"), ("pending", 3, None))
            )
            is Rule.MIXED
        )
        assert classify_registration(rows(("booked", 1, None), ("scheduled", 2, "a"))) is (
            Rule.UNDETERMINED
        )

    def test_custom_vocabulary(self):
        vocab = StatusVocabulary(frozenset({"queued"}), frozenset({"waiting"}), frozenset({"done"}))
        assert classify_registration(rows(("queued", 1, "a"), ("waiting", 2, None)), vocab) is (
            Rule.SINGLE_SCHEDULED
        )


class TestViolations:
    def test_rule_holds(self):
        assert one_at_a_time_violations(rows(("scheduled", 1, "j"), ("pending", 2, None))) == []

    def test_not_exactly_one(self):
        [v] = one_at_a_time_violations(rows(("pending", 1, None), ("pending", 2, None)))
        assert v.startswith("expected exactly one scheduled row, found 0")

    def test_not_the_earliest_and_no_job(self):
        out = one_at_a_time_violations(rows(("pending", 1, None), ("scheduled", 2, "")))
        assert "is not the first pending one" in out[0]
        assert "carries no job id" in out[1]

    def test_unreadable_start(self):
        data = parse_rows([{"id": "x", "status": "scheduled", "start": "soon", "job_id": "j"}])
        assert one_at_a_time_violations(data) == ["the scheduled row x has no readable start"]


def test_silent_stall():
    assert silently_stalled(rows(("booked", 1, None), ("pending", 2, None)))
    assert not silently_stalled(rows(("booked", 1, None), ("scheduled", 2, "j")))
    assert not silently_stalled(rows(("pending", 1, None)))


def test_parse_helpers():
    assert parse_start("2031-05-04T10:00:00Z") == datetime(2031, 5, 4, 10, tzinfo=timezone.utc)
    assert parse_start("2031-05-04T10:00:00") is None  # naive is unreadable, not local time
    assert parse_start(None) is None
    [r] = parse_rows([{"id": 1, "status": "SCHEDULED", "job_id": "null"}])
    assert r.status == "scheduled" and r.job_id is None and r.start is None
    assert parse_rows(None) == []
    assert (
        first_pending(rows(("booked", 1, None), ("pending", 3, None), ("scheduled", 2, "j"))).id
        == "r2"
    )


def test_expected_run_time():
    utc = timezone.utc
    registered = datetime(2031, 5, 4, 10, 0, 0, tzinfo=utc)
    assert expected_run_time(datetime(2031, 5, 1, tzinfo=utc), registered) == registered + (
        timedelta(seconds=5)
    )
    later = datetime(2031, 6, 1, tzinfo=utc)
    assert expected_run_time(later, registered) == later
    with pytest.raises(ValueError):
        expected_run_time(datetime(2031, 6, 1), registered)
