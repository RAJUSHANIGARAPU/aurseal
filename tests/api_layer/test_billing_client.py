"""BillingClient over an offline transport: paths, bodies and the 2xx requirement."""

from __future__ import annotations

import json

import pytest

from api.clients.billing_client import BillingClient
from api.clients.http_engine import ApiError, HttpEngine
from tests.api_layer.fakes import FakeAdapter, make_response, session_with

BASE = "http://billing.example.test/api/"


def client_with(adapter: FakeAdapter) -> BillingClient:
    return BillingClient(HttpEngine(BASE, session=session_with(adapter), max_retries=0))


def test_update_customer_patches_the_customer_record():
    adapter = FakeAdapter().on("PATCH", "/customers/7", make_response(200, {"id": 7}))
    client = client_with(adapter)
    out = client.update_customer(7, type="COMPANY", display_name="Renamed Co")
    assert out == {"id": 7}
    sent = adapter.sent[-1]
    assert sent.url == BASE + "customers/7"
    assert json.loads(sent.body) == {"type": "COMPANY", "display_name": "Renamed Co"}


def test_update_customer_needs_a_field():
    with pytest.raises(ValueError):
        client_with(FakeAdapter()).update_customer(7)


def test_customer_of_resolves_the_holder():
    adapter = (
        FakeAdapter()
        .on("GET", "/subscriptions/3", make_response(200, {"id": 3, "holder": {"id": 9}}))
        .on("GET", "/customers/9", make_response(200, {"id": 9, "name": "Holder"}))
    )
    assert client_with(adapter).customer_of(3) == {"id": 9, "name": "Holder"}


def test_customer_of_without_holder_raises():
    adapter = FakeAdapter().on("GET", "/subscriptions/3", make_response(200, {"id": 3}))
    with pytest.raises(LookupError):
        client_with(adapter).customer_of(3)


def test_draft_calls_use_the_draft_routes():
    adapter = (
        FakeAdapter()
        .on("POST", "/subscriptions/3/drafts", make_response(201, {"id": 11}))
        .on("PATCH", "/drafts/11", make_response(200, {"id": 11}))
        .on("POST", "/drafts/11/actions/accept-ownership", make_response(200, {"id": 11}))
        .on("POST", "/drafts/11/actions/confirm", make_response(200, {"version": 2}))
        .on("POST", "/drafts/11/actions/discard", make_response(200, {"id": 11}))
    )
    client = client_with(adapter)
    assert client.open_draft(3, "change", changes={"x": 1}) == {"id": 11}
    assert json.loads(adapter.sent[-1].body) == {"kind": "change", "changes": {"x": 1}}
    client.patch_draft(11, reason="MOVED")
    client.accept_ownership(11)
    assert client.confirm_draft(11) == {"version": 2}
    client.discard_draft(11)
    assert [r.method for r in adapter.sent] == ["POST", "PATCH", "POST", "POST", "POST"]


def test_version_and_transactions_paths():
    adapter = (
        FakeAdapter()
        .on("GET", "/subscriptions/3/transactions", make_response(200, []))
        .on("GET", "/subscriptions/3?version=2", make_response(200, {"version": 2}))
    )
    client = client_with(adapter)
    assert client.transactions(3) == []
    assert client.subscription(3, version=2) == {"version": 2}


def test_non_2xx_raises_api_error_with_the_response():
    adapter = FakeAdapter().on(
        "PATCH", "/customers/7", make_response(422, {"message": "City is required."})
    )
    with pytest.raises(ApiError) as err:
        client_with(adapter).update_customer(7, billing_address={})
    assert err.value.response.status == 422
