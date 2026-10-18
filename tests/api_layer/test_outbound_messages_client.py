"""OutboundMessagesClient over a scripted transport: reads, polling, download, generate."""

from __future__ import annotations

import itertools
import json

import pytest

from api.clients.http_engine import ApiError, HttpEngine
from api.clients.outbound_messages_client import (
    NotGenerated,
    OutboundMessagesClient,
    StillWaiting,
)
from tests.api_layer.fakes import FakeAdapter, make_response, session_with

BASE = "http://svc.example.test/api/"


def client(adapter, **kw):
    engine = HttpEngine(BASE, session=session_with(adapter), max_retries=0)
    ticks = itertools.count()
    return OutboundMessagesClient(engine, sleep=lambda s: None, clock=lambda: next(ticks), **kw)


def test_overview_is_newest_first_and_passes_filters():
    rows = [{"id": "a", "created_at": "2026-01-01"}, {"id": "b", "created_at": "2026-02-01"}]
    adapter = FakeAdapter().on("GET", "/api/messages", make_response(200, rows))
    found = client(adapter).overview(7, since="2026-01-01T00:00:00Z")
    assert [m["id"] for m in found] == ["b", "a"]
    assert "subscription_id=7" in adapter.sent[0].url and "since=" in adapter.sent[0].url


def test_wait_for_files_polls_until_files_exist():
    answers = iter([{"files": []}, {"files": []}, {"files": [{"type": "XML"}]}])
    adapter = FakeAdapter().on("GET", "/messages/m1", lambda r: make_response(200, next(answers)))
    assert client(adapter).wait_for_files("m1")["files"] == [{"type": "XML"}]
    assert len(adapter.sent) == 3


def test_wait_gives_up_with_the_last_state():
    adapter = FakeAdapter().on(
        "GET", "/messages/m1", make_response(200, {"status": "NEW", "files": []})
    )
    with pytest.raises(StillWaiting, match="last seen: .*NEW"):
        client(adapter, timeout_s=3).wait_for_files("m1")


def test_download_distinguishes_not_generated_from_errors():
    adapter = (
        FakeAdapter()
        .on("GET", "/messages/new/content", make_response(404, {"error": "not_found"}))
        .on("GET", "/messages/bad/content", make_response(500, {"error": "x"}))
        .on("GET", "/messages/ok/content", make_response(200, b"HDR+1'"))
    )
    c = client(adapter)
    with pytest.raises(NotGenerated):
        c.download("new")
    with pytest.raises(ApiError):
        c.download("bad")
    assert c.download("ok", fmt="xml") == b"HDR+1'"
    assert adapter.sent[-1].url.endswith("content?format=xml")


def test_generate_posts_once_and_waits_for_the_job():
    states = iter(["RUNNING", "DONE"])
    adapter = (
        FakeAdapter()
        .on("POST", "/messages/generate", make_response(202, {"job_id": 5, "state": "RUNNING"}))
        .on("GET", "/jobs/5", lambda r: make_response(200, {"id": 5, "state": next(states)}))
    )
    assert client(adapter).generate(9)["state"] == "DONE"
    posted = [r for r in adapter.sent if r.method == "POST"]
    assert len(posted) == 1 and json.loads(posted[0].body) == {"subscription_id": 9}


def test_the_client_cannot_send():
    c = client(FakeAdapter())
    assert not any(hasattr(c, name) for name in ("send", "resend", "deliver"))
