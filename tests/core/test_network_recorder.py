"""Network call recording around a UI action, driven without a browser."""

from __future__ import annotations

import re
from collections import defaultdict
from types import SimpleNamespace

from core.network_recorder import Call, NetworkRecorder


class _Emitter:
    """The on/remove_listener surface of a Playwright page or context."""

    def __init__(self, fail_remove: bool = False) -> None:
        self.listeners = defaultdict(list)
        self.fail_remove = fail_remove

    def on(self, event, listener):
        self.listeners[event].append(listener)

    def remove_listener(self, event, listener):
        if self.fail_remove:
            raise RuntimeError("target closed")
        self.listeners[event].remove(listener)

    def emit(self, event, payload):
        for listener in list(self.listeners[event]):
            listener(payload)

    def request(self, method, url, failure=None):
        req = SimpleNamespace(method=method, url=url, failure=failure)
        self.emit("request", req)
        return req

    def respond(self, req, status):
        self.emit("response", SimpleNamespace(request=req, url=req.url, status=status))

    def fail(self, req):
        self.emit("requestfailed", req)


def test_records_only_while_started_and_only_matching_urls():
    page = _Emitter()
    page.request("GET", "https://app.example.test/api/before")
    with NetworkRecorder(page, match="/api/") as calls:
        save = page.request("PUT", "https://app.example.test/api/orders/ORD-42")
        page.request("GET", "https://app.example.test/static/app.js")
        page.respond(save, 200)
    page.request("GET", "https://app.example.test/api/after")
    assert calls.requests() == [Call("PUT", "https://app.example.test/api/orders/ORD-42")]
    assert calls.responses() == [
        Call("PUT", "https://app.example.test/api/orders/ORD-42", status=200)
    ]
    assert calls.methods_and_paths() == [("PUT", "/api/orders/ORD-42")]
    assert calls.methods_and_paths(responses=True) == [("PUT", "/api/orders/ORD-42")]
    assert not calls.recording
    assert all(not v for v in page.listeners.values())


def test_regex_and_callable_matchers():
    page = _Emitter()
    by_regex = NetworkRecorder(page, match=re.compile(r"/orders/\d+$")).start()
    by_func = NetworkRecorder(page, match=lambda url: url.endswith(".json")).start()
    page.request("GET", "https://x.example.test/orders/7")
    page.request("GET", "https://x.example.test/orders/7/lines")
    page.request("GET", "https://x.example.test/data.json")
    assert [c.path for c in by_regex.requests()] == ["/orders/7"]
    assert [c.path for c in by_func.requests()] == ["/data.json"]


def test_unanswered_failed_and_status_filters():
    page = _Emitter()
    with NetworkRecorder(page) as calls:
        ok = page.request("GET", "https://x.example.test/a")
        page.request("GET", "https://x.example.test/a")  # same call twice, one answer
        bad = page.request("POST", "https://x.example.test/b")
        lost = page.request("GET", "https://x.example.test/c", failure="net::ERR_ABORTED")
        page.respond(ok, 200)
        page.respond(bad, 503)
        page.fail(lost)
    assert [str(c) for c in calls.unanswered()] == [
        "GET https://x.example.test/a",
        "GET https://x.example.test/c",
    ]
    assert calls.failed() == [Call("GET", "https://x.example.test/c", failure="net::ERR_ABORTED")]
    assert calls.with_status(lambda s: s >= 500) == [
        Call("POST", "https://x.example.test/b", status=503)
    ]
    text = calls.describe()
    assert "POST https://x.example.test/b -> 503" in text
    assert "GET https://x.example.test/c  (no response)" in text
    assert "GET https://x.example.test/c -> net::ERR_ABORTED" in text


def test_limit_counts_what_was_dropped():
    page = _Emitter()
    with NetworkRecorder(page, limit=2) as calls:
        for i in range(5):
            page.request("GET", f"https://x.example.test/{i}")
    assert len(calls.requests()) == 2
    assert calls.dropped == 3
    assert "3 more calls not kept" in calls.describe()


def test_stop_never_raises_on_a_closed_target_and_start_is_idempotent():
    page = _Emitter(fail_remove=True)
    recorder = NetworkRecorder(page).start().start()
    assert len(page.listeners["request"]) == 1
    recorder.stop()
    assert not recorder.recording


def test_empty_and_clear():
    page = _Emitter()
    with NetworkRecorder(page) as calls:
        page.request("GET", "https://x.example.test/a")
    assert calls.describe() == "GET https://x.example.test/a  (no response)"
    calls.clear()
    assert calls.requests() == []
    assert calls.describe() == "(no matching calls)"
