"""BillingRunner over a scripted transport: booking loops, refusals and polling."""

from __future__ import annotations

import json

import pytest
import requests

from api.clients.billing_runner import BillingError, BillingRunner
from api.clients.http_engine import HttpEngine
from tests.api_layer.fakes import FakeAdapter, make_response

BASE = "http://billing.example.test/api/"


def client(adapter: FakeAdapter, **kw) -> BillingRunner:
    session = requests.Session()
    session.mount("http://", adapter)
    return BillingRunner(HttpEngine(BASE, session=session, max_retries=0), **kw)


def test_process_all_due_books_until_nothing_is_scheduled():
    queue = [{"id": 1, "state": "BOOKED"}, {"id": 2, "state": "BOOKED"}]

    def next_due(request):
        assert json.loads(request.body) == {"subscription_id": 7}
        return make_response(200, queue.pop(0)) if queue else make_response(422, {"error": "x"})

    adapter = FakeAdapter().on("POST", "/jobs/billing/run-next", next_due)
    assert [t["id"] for t in client(adapter).process_all_due(7)] == [1, 2]


def test_a_refusal_other_than_nothing_due_raises_with_the_response():
    adapter = FakeAdapter().on("POST", "/run-next", make_response(500, {"error": "boom"}))
    with pytest.raises(BillingError, match="processing the next transaction of 7 failed"):
        client(adapter).process_next_due(7)


def test_a_chain_that_never_ends_is_reported():
    adapter = FakeAdapter().on("POST", "/run-next", make_response(200, {"id": 1}))
    with pytest.raises(AssertionError, match="does not end"):
        client(adapter).process_all_due(7, limit=3)


def test_run_due_sends_until_and_entity_and_returns_booked_ids():
    seen = {}

    def run(request):
        seen.update(json.loads(request.body))
        return make_response(200, {"booked": [4, 5]})

    adapter = FakeAdapter().on("POST", "/jobs/run", run)
    assert client(adapter).run_due(until="2026-05-01", entity_id=3) == [4, 5]
    assert seen == {"until": "2026-05-01", "subscription_id": 3}


def test_paths_and_fields_are_configurable():
    adapter = FakeAdapter().on("POST", "/orders/process-next", make_response(200, {"id": 9}))
    c = client(adapter, run_next_path="orders/process-next", entity_field="order_id")
    assert c.process_next_due(1) == {"id": 9}
    assert json.loads(adapter.sent[0].body) == {"order_id": 1}


def test_await_transaction_polls_until_a_match_and_reports_the_last_seen_on_timeout():
    answers = [
        [{"kind": "NEW", "state": "BOOKED"}],
        [{"kind": "NEW", "state": "BOOKED"}, {"kind": "CANCEL", "state": "SCHEDULED", "id": 3}],
    ]
    adapter = FakeAdapter().on(
        "GET", "/transactions", lambda r: make_response(200, answers.pop(0) if answers else [])
    )
    sleeps = []
    tx = client(adapter).await_transaction(
        1, kind="CANCEL", states={"SCHEDULED"}, sleep=sleeps.append, timeout_s=10
    )
    assert tx["id"] == 3 and sleeps == [0.5]

    ticks = iter([0.0, 0.0, 99.0])
    stuck = FakeAdapter().on(
        "GET", "/transactions", make_response(200, [{"kind": "NEW", "state": "BOOKED"}])
    )
    with pytest.raises(AssertionError, match=r"no CANCEL in \['BOOKED'\].*last seen: NEW=BOOKED"):
        client(stuck).await_transaction(
            1, kind="CANCEL", states={"BOOKED"}, sleep=lambda s: None, clock=lambda: next(ticks)
        )
