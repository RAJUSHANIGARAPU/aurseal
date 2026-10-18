"""One-at-a-time registration, read through a read-only client.

In one-at-a-time mode only the first pending transaction carries a booking job; booking it
registers the next one.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import requests

from api.clients.billing_runner import BillingRunner
from gate.read_only import ReadOnlyClient, find_write_calls
from oracles.job_registration import Rule, one_at_a_time_violations
from tests.demo_examples.scheduler import _chain


@pytest.fixture
def read_only(demo_sut):
    session = requests.Session()
    yield ReadOnlyClient.over(session, demo_sut.base_url)
    session.close()


def one_at_a_time(demo_sut):
    demo_sut.client.ok("POST", "/api/_admin/scheduler-mode", {"mode": "one_at_a_time"})


def test_registration_rules_abstain_while_every_transaction_is_registered(
    demo_seed, read_only, frozen
):
    sub = demo_seed.subscription(start_date="2026-04-01")  # default mode: register all
    with pytest.raises(pytest.skip.Exception, match="registers every transaction"):
        _chain.require_one_at_a_time(_chain.schedule(read_only, sub["id"]))


def test_one_at_a_time_registration_rules(demo_sut, demo_seed, demo_http, read_only, frozen):
    one_at_a_time(demo_sut)
    sub = demo_seed.subscription(start_date="2026-04-01")  # bound, nothing booked yet
    rows = _chain.schedule(read_only, sub["id"])
    assert _chain.require_one_at_a_time(rows) is Rule.SINGLE_SCHEDULED

    # exactly one SCHEDULED, the earliest, with a job; every other one INITIAL
    assert one_at_a_time_violations(rows, _chain.VOCAB) == []
    first, *rest = rows
    assert first.status == "scheduled" and first.job_id
    assert rest and all(r.status == "initial" and r.job_id is None for r in rest)
    job = demo_sut.client.ok("GET", f"/api/jobs/{first.job_id}")
    assert job["due_date"] == first.start.date().isoformat()

    # booking the registered one advances the chain by exactly one
    BillingRunner(demo_http).process_next_due(sub["id"])
    after = _chain.schedule(read_only, sub["id"])
    assert after[0].status == "booked"
    assert one_at_a_time_violations(after, _chain.VOCAB) == []
    assert after[1].status == "scheduled" and after[1].job_id
    assert all(r.status == "initial" for r in after[2:])
    next_job = demo_sut.client.ok("GET", f"/api/jobs/{after[1].job_id}")
    assert next_job["due_date"] == after[1].start.date().isoformat()


def test_the_chain_probe_cannot_write():
    assert find_write_calls([Path(_chain.__file__)]) == []
