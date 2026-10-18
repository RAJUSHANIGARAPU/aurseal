"""Tests for api.clients.http_engine and api.clients.run_tagging (offline transport)."""

from __future__ import annotations

from datetime import datetime

import pytest

from api.clients import run_tagging
from api.clients.http_engine import ApiError, ApiResponse, HttpEngine, Part
from tests.api_layer.fakes import FakeAdapter, make_response, session_with

BASE = "https://sut.example/api/"


def _engine(adapter: FakeAdapter, **kw) -> HttpEngine:
    return HttpEngine(BASE, session=session_with(adapter), **kw)


class TestApiResponse:
    def test_headers_are_case_insensitive(self):
        r = ApiResponse(201, b"{}", {"location": "/items/abc"})
        assert r.header("LOCATION") == "/items/abc"
        assert r.location == "/items/abc"

    def test_location_id_prefers_the_last_uuid(self):
        loc = "/a/11111111-1111-1111-1111-111111111111/b/22222222-2222-2222-2222-222222222222?x=1"
        assert ApiResponse(201, headers={"Location": loc}).location_id == (
            "22222222-2222-2222-2222-222222222222"
        )

    def test_location_id_falls_back_to_last_segment(self):
        assert ApiResponse(201, headers={"Location": "/items/42/"}).location_id == "42"
        assert ApiResponse(201).location_id is None

    def test_success_range_and_json(self):
        assert ApiResponse(204).is_success
        assert not ApiResponse(302).is_success
        assert ApiResponse(200, b'{"a": 1}').json() == {"a": 1}
        with pytest.raises(ValueError):
            ApiResponse(200, b"").json()

    def test_str_is_truncated_for_assertion_messages(self):
        text = str(ApiResponse(500, b"x" * 5000, url="u"))
        assert len(text) < 900 and "5000 bytes" in text

    def test_raise_for_status_carries_the_response(self):
        with pytest.raises(ApiError) as exc:
            ApiResponse(422, b'{"field": "bad"}').raise_for_status()
        assert exc.value.response.status == 422 and "bad" in str(exc.value)


class TestHttpEngine:
    def test_joins_paths_and_sends_auth_and_run_tag(self, monkeypatch):
        monkeypatch.setenv("TEST_RUN_ID", "run 42/ci")
        adapter = FakeAdapter().on("GET", "/api/items", make_response(200, {"ok": True}))
        r = _engine(adapter, token="t0k").get("/items", params={"q": "x"})
        sent = adapter.sent[0]
        assert r.json() == {"ok": True}
        assert sent.url == "https://sut.example/api/items?q=x"
        assert sent.headers["Authorization"] == "Bearer t0k"
        assert sent.headers[run_tagging.HEADER] == "run-42-ci"
        assert "run-42-ci" in sent.headers["User-Agent"]

    def test_tagging_can_be_switched_off(self, monkeypatch):
        monkeypatch.setenv("TEST_RUN_TAGGING", "false")
        adapter = FakeAdapter().on("GET", "/", make_response(200))
        _engine(adapter).get("x")
        assert run_tagging.HEADER not in adapter.sent[0].headers

    def test_absolute_url_is_used_verbatim(self):
        adapter = FakeAdapter().on("GET", "other.example", make_response(200))
        _engine(adapter).get("https://other.example/file")
        assert adapter.sent[0].url == "https://other.example/file"

    def test_multipart_upload_carries_json_and_file_parts(self):
        adapter = FakeAdapter().on("POST", "/upload", make_response(201))
        _engine(adapter).post_multipart(
            "upload",
            [
                Part.json("meta", {"name": "a"}),
                Part.file("doc", "a.pdf", "application/pdf", b"%PDF"),
            ],
        )
        sent = adapter.sent[0]
        assert sent.headers["Content-Type"].startswith("multipart/form-data")
        assert b'{"name": "a"}' in sent.body and b'filename="a.pdf"' in sent.body

    def test_binary_download_and_failure(self):
        adapter = (
            FakeAdapter()
            .on("GET", "/ok.pdf", make_response(200, b"%PDF-1.7"))
            .on("GET", "/gone.pdf", make_response(404, b"nope"))
        )
        engine = _engine(adapter)
        assert engine.get_bytes("ok.pdf") == b"%PDF-1.7"
        assert adapter.sent[0].headers["Accept"] == "application/octet-stream"
        with pytest.raises(ApiError):
            engine.get_bytes("gone.pdf")

    def test_observers_see_every_response_and_cannot_break_it(self):
        adapter = FakeAdapter().on("POST", "/items", make_response(201, {"id": "7"}))
        engine = _engine(adapter)
        seen = []
        engine.observe(lambda m, u, r: seen.append((m, r.status)))
        engine.observe(lambda m, u, r: 1 / 0)
        assert engine.post("items", json={"a": 1}).status == 201
        assert seen == [("POST", 201)]


class TestRunTagging:
    def test_resolution_order(self):
        now = datetime(2026, 1, 2, 3, 4, 5)
        assert run_tagging.resolve({"TEST_RUN_ID": "a", "BUILD_TAG": "b"}, "h", now) == "a"
        # A GitHub id carries the attempt, so a re-run is a distinct run.
        assert run_tagging.resolve({"GITHUB_RUN_ID": "99"}, "h", now) == "gh-99-1"
        assert run_tagging.resolve({}, "host", now) == "local-host-20260102-030405"

    def test_ids_are_bounded_and_header_safe(self):
        rid = run_tagging.resolve({"TEST_RUN_ID": "ä b" * 50}, "h", datetime.now())
        assert len(rid) <= run_tagging.MAX_LENGTH
        assert all(c.isalnum() or c in "._-" for c in rid)

    def test_browser_user_agent_appends_never_replaces(self, monkeypatch):
        monkeypatch.setenv("TEST_RUN_ID", "r1")
        monkeypatch.delenv("TEST_RUN_PRODUCT", raising=False)
        assert run_tagging.browser_user_agent("Mozilla/5.0") == "Mozilla/5.0 test-automation/r1"
        assert run_tagging.browser_user_agent(None) is None
