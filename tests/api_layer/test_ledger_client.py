"""Tests for api.clients.ledger_client (offline transport)."""

from __future__ import annotations

import json
from unittest import mock

import pytest

from api.clients.http_engine import ApiError, HttpEngine
from api.clients.ledger_client import LedgerClient, LedgerPaths
from tests.api_layer.fakes import FakeAdapter, make_response, session_with

BASE = "https://billing.example.test/api/"


def _client(adapter: FakeAdapter, **kw) -> LedgerClient:
    return LedgerClient(HttpEngine(BASE, session=session_with(adapter)), **kw)


def test_invoice_with_journal_picks_by_number():
    adapter = (
        FakeAdapter()
        .on(
            "GET",
            "/subscriptions/5/invoices",
            make_response(200, [{"id": 2, "number": "100002"}, {"id": 1, "number": "100001"}]),
        )
        .on("GET", "/invoices/1/journal", make_response(200, [{"id": 9}]))
    )
    invoice, journal = _client(adapter, attach=False).invoice_with_journal(5)
    assert invoice["id"] == 1 and journal == [{"id": 9}]
    with pytest.raises(AssertionError, match="has 2 invoice"):
        _client(adapter, attach=False).invoice_with_journal(5, index=2)


def test_reads_and_query_parameters():
    adapter = (
        FakeAdapter()
        .on("GET", "/journal?references=TX-1%2CTX-2", make_response(200, [{"id": 1}]))
        .on("GET", "/ledger-codes", make_response(200, {"FEES": "8100"}))
        .on("GET", "/accounts", make_response(200, [{"id": 1, "managed": True}]))
        .on("GET", "/settlements?party=3", make_response(200, []))
        .on("GET", "/statements?settlement=4", make_response(200, {"entries": []}))
        .on("GET", "/subscriptions/5/transactions", make_response(200, []))
        .on("GET", "/invoices/1", make_response(200, {"id": 1}))
    )
    client = _client(adapter, attach=False)
    assert client.journal(["TX-1", "TX-2"]) == [{"id": 1}]
    assert client.ledger_codes() == {"FEES": "8100"}
    assert client.accounts()[0]["managed"] is True
    assert client.settlements(party=3) == []
    assert client.statement(4).json() == {"entries": []}
    assert client.transactions(5) == []
    assert client.invoice(1) == {"id": 1}


def test_writes():
    adapter = (
        FakeAdapter()
        .on(
            "POST",
            "/subscriptions/5/invoices/process?until=2026-04-01",
            make_response(200, {"booked": [1]}),
        )
        .on("PATCH", "/accounts/2", make_response(409, {"message": "managed"}))
        .on("POST", "/settlements", make_response(201, {"id": 4}))
    )
    client = _client(adapter, attach=False)
    assert client.process_invoices(5, "2026-04-01") == {"booked": [1]}
    assert client.rename_account(2, "X").status == 409
    assert json.loads(adapter.sent[1].body) == {"name": "X"}
    assert client.create_settlement({"partner_id": 1}) == {"id": 4}


def test_failed_read_raises_api_error():
    adapter = FakeAdapter().on("GET", "/invoices/1", make_response(404, {"error": "not_found"}))
    with pytest.raises(ApiError):
        _client(adapter, attach=False).invoice(1)


def test_every_response_is_attached_to_the_report():
    adapter = FakeAdapter().on("GET", "/accounts", make_response(200, [{"id": 1}]))
    with mock.patch("allure.attach") as attach:
        _client(adapter).accounts()
    body = attach.call_args.args[0]
    assert body.startswith(f"GET {BASE}accounts\nHTTP 200") and '"id": 1' in body
    assert attach.call_args.kwargs["name"] == "GET accounts -> 200"


def test_paths_are_configurable():
    adapter = FakeAdapter().on("GET", "/finance/v2/accounts", make_response(200, []))
    client = _client(adapter, attach=False, paths=LedgerPaths(accounts="finance/v2/accounts"))
    assert client.accounts() == []
