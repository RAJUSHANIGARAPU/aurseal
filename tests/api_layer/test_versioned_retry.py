"""Tests for api.clients.versioned_retry — above all, that a landed write is never re-sent."""

from __future__ import annotations

import pytest
import requests

from api.clients import versioned_retry as vr
from api.clients.http_engine import ApiResponse, HttpEngine
from tests.api_layer.fakes import FakeAdapter, make_response, session_with

ENTITY = "/api/entities/abc"
TARGET = vr.VersionedTarget(ENTITY, 5)
read_version = vr.version_reader("version")


def entity(version: int) -> ApiResponse:
    return ApiResponse(200, f'{{"version": {version}}}'.encode())


class Script:
    """Returns the scripted outcomes in order and counts the calls."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        out = self.outcomes.pop(0)
        if isinstance(out, BaseException):
            raise out
        return out


def run(send, read, **kw):
    sleeps = []
    result = vr.execute(
        TARGET, send, read, read_version=read_version, sleep=sleeps.append, backoff_s=1, **kw
    )
    return result, sleeps


class TestMatcher:
    match = staticmethod(vr.query_version_matcher(exclude=("/bff/",)))

    @pytest.mark.parametrize(
        "method,url",
        [
            ("PUT", "https://h/api/entities/abc/actions/save?version=3"),
            ("POST", "https://h/api/entities/abc/approve/submit?x=1&version=3"),
        ],
    )
    def test_recognises_versioned_writes(self, method, url):
        assert self.match(method, url) == vr.VersionedTarget("/api/entities/abc", 3)

    @pytest.mark.parametrize(
        "method,url",
        [
            ("GET", "https://h/api/entities/abc/actions/save?version=3"),
            ("POST", "https://h/api/entities/abc/actions/save"),
            ("POST", "https://h/api/entities/abc/actions/save?version=x"),
            ("POST", "https://h/api/bff/entities/abc/actions/save?version=3"),
            ("POST", "https://h/api/entities?version=3"),
        ],
    )
    def test_ignores_everything_else(self, method, url):
        assert self.match(method, url) is None


class TestExecute:
    def test_success_is_returned_untouched(self):
        send, read = Script(ApiResponse(200, b"{}")), Script()
        result, sleeps = run(send, read)
        assert result.status == 200 and send.calls == 1 and read.calls == 0 and sleeps == []

    @pytest.mark.parametrize("status", [500, 400, 409, 422])
    def test_non_gateway_errors_are_not_retried(self, status):
        send, read = Script(ApiResponse(status)), Script()
        result, _ = run(send, read)
        assert result.status == status and send.calls == 1 and read.calls == 0

    def test_gateway_error_but_version_moved_means_applied_and_not_resent(self):
        send, read = Script(ApiResponse(504)), Script(entity(6))
        result, sleeps = run(send, read)
        assert send.calls == 1, "a write proven landed must never be sent twice"
        assert read_version(result) == 6 and sleeps == [1]

    def test_gateway_error_with_unmoved_version_is_resent_at_same_version(self):
        send = Script(ApiResponse(502), ApiResponse(200, b'{"version": 6}'))
        read = Script(entity(5))
        result, sleeps = run(send, read)
        assert send.calls == 2 and result.status == 200 and sleeps == [1]

    def test_gives_up_after_max_retries_with_linear_backoff(self):
        send = Script(*[ApiResponse(503)] * 3)
        read = Script(*[entity(5)] * 3)
        result, sleeps = run(send, read, max_retries=2)
        assert result.status == 503 and send.calls == 3 and sleeps == [1, 2, 3]

    def test_conflict_on_resend_that_landed_is_resolved_by_the_version(self):
        send = Script(ApiResponse(504), ApiResponse(409))
        read = Script(entity(5), entity(6))
        result, _ = run(send, read)
        assert send.calls == 2 and read_version(result) == 6

    def test_conflict_on_resend_that_did_not_land_is_returned(self):
        send = Script(ApiResponse(504), ApiResponse(409))
        read = Script(entity(5), entity(5))
        result, _ = run(send, read)
        assert result.status == 409 and send.calls == 2

    def test_unreadable_entity_is_treated_as_not_applied(self):
        send = Script(ApiResponse(504), ApiResponse(200))
        read = Script(ApiResponse(500))
        result, _ = run(send, read)
        assert result.status == 200 and send.calls == 2

    def test_read_timeout_is_handled_like_a_504(self):
        send = Script(requests.exceptions.ReadTimeout("slow"))
        read = Script(entity(6))
        result, _ = run(send, read)
        assert read_version(result) == 6

    def test_read_timeout_is_reraised_when_retries_run_out(self):
        send = Script(*[requests.exceptions.ReadTimeout("slow")] * 2)
        read = Script(entity(5), entity(5))
        with pytest.raises(requests.exceptions.ReadTimeout):
            run(send, read, max_retries=1)

    def test_connect_timeout_propagates_immediately(self):
        send, read = Script(requests.exceptions.ConnectTimeout("down")), Script()
        with pytest.raises(requests.exceptions.ConnectTimeout):
            run(send, read)
        assert read.calls == 0

    def test_zero_retries_still_checks_whether_it_landed(self):
        send, read = Script(ApiResponse(504)), Script(entity(5))
        result, _ = run(send, read, max_retries=0)
        assert result.status == 504 and send.calls == 1 and read.calls == 1


def test_engine_wires_the_retry_for_matching_calls_only():
    state = {"version": 5, "sends": 0}

    def action(_req):
        state["sends"] += 1
        state["version"] += 1  # the write lands ...
        return make_response(504)  # ... but the gateway gives up on it

    adapter = (
        FakeAdapter()
        .on("PUT", "/actions/", action)
        .on("GET", "/entities/abc", lambda _r: make_response(200, {"version": state["version"]}))
        .on("POST", "/items", make_response(504))
    )
    engine = HttpEngine(
        "https://h/api/",
        session=session_with(adapter),
        versioned=vr.query_version_matcher(),
        sleep=lambda _s: None,
    )
    result = engine.put("entities/abc/actions/save?version=5", json={})
    assert state["sends"] == 1 and result.json() == {"version": 6}
    assert engine.post("items", json={}).status == 504
    assert sum(1 for r in adapter.sent if "/items" in r.url) == 1
