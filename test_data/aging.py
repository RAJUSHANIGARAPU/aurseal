"""Fixtures whose meaning depends on a date, and the day that meaning expires.

A record that "starts in the future" stops being one the day its start date
passes. Two rules keep such a test honest:

* **Seed it per run** with a date computed from today in the business zone,
  anchored to the first of a later month
  (``RUN_MONTH_START_PLUS_<n>M`` in :mod:`test_data.fixture_replay`), and
  assert that the seeded date equals the requested one
  (:func:`assert_seeded_date`): a service that clamps or shifts the date has
  produced a different fixture than the test asked for.
* **A hand-made fixture passed by id** cannot be re-dated, so the test abstains
  once its date is no longer after today (:func:`require_still_future`) rather
  than asserting future-only behaviour on a record that is now current.
"""

from __future__ import annotations

from datetime import date

from test_data.fixture_replay import SeedError
from verification.evidence_verdict import EvidenceVerdict


def _as_date(value: date | str) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise ValueError(f"{value!r} is not an ISO date") from exc


def require_still_future(
    entity_date: date | str,
    today: date,
    reason: str,
    *,
    verdict: EvidenceVerdict | None = None,
) -> date:
    """Return the date when it is after ``today``; otherwise abstain with ``reason``."""
    day = _as_date(entity_date)
    if day <= today:
        coverage = verdict or EvidenceVerdict("time-relative fixture")
        coverage.no_verdict(
            f"{reason}: its date {day.isoformat()} is no longer after today "
            f"({today.isoformat()}); re-seed it per run instead of pinning it"
        )
    return day


def assert_seeded_date(requested: date | str, seeded: date | str, *, what: str) -> date:
    """Raise :class:`SeedError` unless the service stored exactly the requested date."""
    want, got = _as_date(requested), _as_date(seeded)
    if want != got:
        raise SeedError(
            f"{what}: asked for {want.isoformat()}, the service stored {got.isoformat()}; the "
            "fixture is not the one the test describes"
        )
    return got
