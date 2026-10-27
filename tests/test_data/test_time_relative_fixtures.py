"""Tests for time-relative per-run fixtures: month-start anchors and expiring hand-made dates."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from test_data.aging import assert_seeded_date, require_still_future
from test_data.fixture_replay import (
    Fixture,
    SeedError,
    month_start_plus,
    run_day_values,
    unbound_placeholders,
)


@pytest.fixture(autouse=True)
def isolated_verdicts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VERDICTS_FILE", str(tmp_path / "verdicts.jsonl"))
    monkeypatch.delenv("COVERAGE_VERDICT_REQUIRED", raising=False)


@pytest.mark.parametrize(
    ("today", "months", "expected"),
    [
        (date(2031, 1, 31), 1, date(2031, 2, 1)),  # no clamping from the 31st
        (date(2031, 11, 15), 2, date(2032, 1, 1)),  # year roll-over
        (date(2032, 2, 29), 12, date(2033, 2, 1)),
        (date(2031, 5, 1), 0, date(2031, 5, 1)),
    ],
)
def test_month_start_plus(today, months, expected):
    assert month_start_plus(today, months) == expected
    assert run_day_values(today)(f"RUN_MONTH_START_PLUS_{months}M") == expected.isoformat()


def test_existing_tokens_still_resolve_and_unknown_ones_do_not():
    resolve = run_day_values(date(2031, 3, 31))
    assert resolve("TODAY_TOKEN") == "2031-03-31"
    assert resolve("RUN_DAY_DATE_PLUS_1D") == "2031-04-01"
    assert resolve("RUN_MONTH_START_PLUS_M") is None
    assert resolve("RUN_MONTH_START_MINUS_1M") is None


def test_month_start_token_is_a_builtin_for_the_offline_check():
    fixture = Fixture.from_dict(
        {"steps": [{"method": "POST", "path": "/x", "body": {"d": "${RUN_MONTH_START_PLUS_2M}"}}]}
    )
    assert unbound_placeholders(fixture, set()) == set()


def test_future_date_passes_through():
    assert require_still_future("2031-04-01", date(2031, 3, 31), "future start") == date(2031, 4, 1)


@pytest.mark.parametrize("entity_date", ["2031-03-31", date(2031, 3, 1)])
def test_a_date_that_is_no_longer_future_abstains(entity_date):
    with pytest.raises(pytest.skip.Exception, match="no longer after today"):
        require_still_future(entity_date, date(2031, 3, 31), "hand-made future start")


def test_seeded_date_must_equal_the_requested_one():
    assert assert_seeded_date("2031-04-01", "2031-04-01T00:00:00Z", what="start") == date(
        2031, 4, 1
    )
    with pytest.raises(SeedError, match="asked for 2031-04-01, the service stored 2031-03-31"):
        assert_seeded_date(date(2031, 4, 1), "2031-03-31", what="start")
    with pytest.raises(ValueError):
        assert_seeded_date("soon", "2031-03-31", what="start")
