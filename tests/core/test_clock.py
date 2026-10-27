"""Tests for core.clock: 'today' in the business timezone, pinned inside the risky window."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from core import clock
from core.config_reader import ConfigReader, ConfigSources
from core.env_profile import EnvProfile
from test_data.fixture_replay import run_day_values

ZONE = clock.zone_named("Europe/Berlin")  # UTC+1 in winter, UTC+2 in summer


def _config(**values: str) -> ConfigReader:
    return ConfigReader("qa", sources=ConfigSources(os_env=values, file_config={}))


@pytest.mark.parametrize(
    ("utc_instant", "business_day"),
    [
        ("2031-07-01T21:59:59+00:00", date(2031, 7, 1)),  # 23:59:59 local
        ("2031-07-01T22:00:00+00:00", date(2031, 7, 2)),  # local midnight, UTC still the 1st
        ("2031-07-01T23:30:00+00:00", date(2031, 7, 2)),  # inside 00:00-02:00 local
        ("2031-07-02T00:00:00+00:00", date(2031, 7, 2)),  # UTC catches up
    ],
)
def test_today_follows_the_business_zone_through_the_window(utc_instant, business_day):
    moment = datetime.fromisoformat(utc_instant)
    assert clock.today(zone=ZONE, now=lambda: moment) == business_day


def test_a_utc_runner_is_a_day_behind_in_the_window():
    moment = datetime(2031, 7, 1, 23, 30, tzinfo=timezone.utc)
    assert clock.today(zone=timezone.utc, now=lambda: moment) == date(2031, 7, 1)
    assert clock.today(zone=ZONE, now=lambda: moment) == date(2031, 7, 2)


def test_local_date_of_a_stored_instant():
    stored = datetime(2031, 1, 31, 23, 15, tzinfo=timezone.utc)
    assert clock.local_date(stored, zone=ZONE) == date(2031, 2, 1)
    offset = datetime(2031, 1, 31, 23, 15, tzinfo=timezone(timedelta(hours=-5)))
    assert clock.local_date(offset, zone=ZONE) == date(2031, 2, 1)


def test_offset_less_instants_raise():
    with pytest.raises(clock.NaiveInstant):
        clock.local_date(datetime(2031, 1, 1, 0, 30), zone=ZONE)
    with pytest.raises(clock.NaiveInstant):
        clock.today(zone=ZONE, now=lambda: datetime(2031, 1, 1))


def test_zone_comes_from_config_and_defaults_to_utc():
    assert clock.business_zone(_config()) is timezone.utc
    assert str(clock.business_zone(_config(BUSINESS_TIMEZONE="Europe/Berlin"))) == "Europe/Berlin"
    with pytest.raises(ValueError, match="not a known IANA timezone"):
        clock.business_zone(_config(BUSINESS_TIMEZONE="Mars/Base"))


def test_default_zone_is_read_when_none_is_passed(monkeypatch):
    monkeypatch.setenv("BUSINESS_TIMEZONE", "Europe/Berlin")
    moment = datetime(2031, 7, 1, 23, 30, tzinfo=timezone.utc)
    assert clock.today(now=lambda: moment) == date(2031, 7, 2)


def test_run_day_values_defaults_to_the_business_today(monkeypatch):
    monkeypatch.setattr(clock, "today", lambda **_: date(2031, 7, 2))
    assert run_day_values()("TODAY_TOKEN") == "2031-07-02"


def test_env_profile_carries_the_zone():
    base = {
        "base_url": "http://localhost:8800/",
        "api_base_url": "http://localhost:8800/api/",
        "browser": "chromium",
        "headless": "true",
        "timeout_ms": "1000",
        "slow_mo_ms": "0",
        "video": "off",
        "tracing": "off",
        "ai_model": "m",
        "ai_timeout_s": "5",
        "read_only": "false",
        "api_assertions_enabled": "true",
    }

    def profile(**extra):
        values = {k.upper(): v for k, v in {**base, **extra}.items()}
        return EnvProfile.from_config(
            ConfigReader("qa", sources=ConfigSources(os_env=values, file_config={}))
        )

    assert profile().business_timezone == "UTC"
    assert profile(business_timezone="Europe/Berlin").business_timezone == "Europe/Berlin"
    with pytest.raises(ValueError):
        profile(business_timezone="Nowhere/Land")
