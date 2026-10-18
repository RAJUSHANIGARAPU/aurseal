"""A subscription that starts next year: confirmed, well numbered, scheduled in its own year."""

from __future__ import annotations

import os
import re

import pytest

from tests.demo_examples.lifecycle import support

#: Point the example at an existing future-start subscription instead of seeding one.
OVERRIDE_ENV = "DEMO_FUTURE_START_SUBSCRIPTION_ID"
START = "2027-01-15"


@pytest.fixture
def future_start(demo_http, demo_seed, frozen):
    override = os.environ.get(OVERRIDE_ENV, "").strip()
    if override:
        found = demo_http.get(f"subscriptions/{override}")
        if found.status == 404:
            pytest.skip(f"{OVERRIDE_ENV}={override} does not exist on this instance")
        sub = support.ok(found)
        if sub["start_date"][:4] <= support.TODAY.isoformat()[:4]:
            pytest.skip(f"{OVERRIDE_ENV}={override} does not start in a later year")
        return sub
    return demo_seed.subscription(start_date=START)


def test_future_start_subscription_numbers_and_year(demo_http, future_start):
    sub = support.subscription(demo_http, future_start["id"])
    assert sub["status"] == "active"
    assert re.fullmatch(r"SUB-\d{6}", sub["number"]), sub["number"]
    start_year = sub["start_date"][:4]
    first = min(support.transactions(demo_http, sub["id"]), key=lambda t: t["period_start"])
    assert first["period_start"][:4] == start_year
    assert first["period_start"][:4] != str(support.TODAY.year), "scheduled in the current year"
