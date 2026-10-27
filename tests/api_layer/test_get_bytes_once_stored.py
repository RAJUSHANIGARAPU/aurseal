"""Download a file only once it is stored: polls on 404, fails fast on anything else.

Runs against a real loopback HTTP server that answers from a script, so the engine's
own transport (requests) is exercised end to end.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from api.clients.document_service import DocumentServiceClient
from api.clients.http_engine import ApiError, HttpEngine


class _Script:
    """Answers each GET with the next (status, body); the last answer repeats."""

    def __init__(self) -> None:
        self.answers: list[tuple[int, bytes]] = []
        self.hits: list[str] = []
        self.lock = threading.Lock()

    def next(self, path: str) -> tuple[int, bytes]:
        with self.lock:
            self.hits.append(path)
            if len(self.answers) > 1:
                return self.answers.pop(0)
            return self.answers[0]


@pytest.fixture
def stub() -> Iterator[tuple[str, _Script]]:
    script = _Script()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - http.server API
            status, body = script.next(self.path)
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:  # keep the test output quiet
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/api/", script
    finally:
        server.shutdown()
        server.server_close()


class _Clock:
    """A monotonic clock the engine's sleep advances, so no test really waits."""

    def __init__(self) -> None:
        self.now = 100.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _engine(base: str, clock: _Clock) -> HttpEngine:
    return HttpEngine(base, sleep=clock.sleep, timeout_s=5)


def test_404_404_200_returns_the_bytes(stub):
    base, script = stub
    script.answers = [(404, b"not yet"), (404, b"not yet"), (200, b"%PDF-1.7 stored")]
    clock = _Clock()
    data = _engine(base, clock).get_bytes_once_stored(
        "files/7", timeout_s=10, interval_s=2, clock=clock
    )
    assert data == b"%PDF-1.7 stored"
    assert len(script.hits) == 3 and clock.sleeps == [2, 2]


def test_404_past_the_window_raises_the_last_404(stub):
    base, script = stub
    script.answers = [(404, b"still missing")]
    clock = _Clock()
    with pytest.raises(ApiError) as raised:
        _engine(base, clock).get_bytes_once_stored(
            "files/7", timeout_s=5, interval_s=2, clock=clock
        )
    assert raised.value.response.status == 404
    assert "still missing" in str(raised.value)
    # 0s, 2s, 4s, then a last attempt at the deadline (5s); nothing after it.
    assert clock.sleeps == [2, 2, 1] and len(script.hits) == 4


def test_500_raises_at_once_without_a_retry(stub):
    base, script = stub
    script.answers = [(500, b"boom")]
    clock = _Clock()
    with pytest.raises(ApiError) as raised:
        _engine(base, clock).get_bytes_once_stored("files/7", timeout_s=30, clock=clock)
    assert raised.value.response.status == 500
    assert len(script.hits) == 1 and clock.sleeps == []


def test_aliases_and_a_custom_not_yet_status(stub):
    base, script = stub
    script.answers = [(409, b"processing"), (200, b"done")]
    clock = _Clock()
    data = _engine(base, clock).get_bytes_once_stored(
        "files/7", window_s=3, pause_s=1, not_yet={404, 409}, clock=clock
    )
    assert data == b"done" and clock.sleeps == [1]


def test_an_interrupt_during_the_pause_propagates(stub):
    base, script = stub
    script.answers = [(404, b"")]

    def interrupted(_seconds: float) -> None:
        raise KeyboardInterrupt

    engine = HttpEngine(base, sleep=interrupted, timeout_s=5)
    with pytest.raises(KeyboardInterrupt):
        engine.get_bytes_once_stored("files/7", timeout_s=30)
    assert len(script.hits) == 1


def test_negative_window_is_a_usage_error(stub):
    base, _script = stub
    with pytest.raises(ValueError):
        HttpEngine(base).get_bytes_once_stored("files/7", timeout_s=-1)


def test_document_client_file_waits_for_storage(stub):
    base, script = stub
    script.answers = [(404, b""), (200, b"%PDF-1.7 late")]
    clock = _Clock()
    client = DocumentServiceClient(_engine(base, clock), store_interval_s=0.5)
    assert client.file(9) == b"%PDF-1.7 late"
    assert script.hits == ["/api/files/9", "/api/files/9"] and clock.sleeps == [0.5]


def test_document_client_file_with_no_wait_asks_once(stub):
    base, script = stub
    script.answers = [(404, b"")]
    client = DocumentServiceClient(_engine(base, _Clock()))
    with pytest.raises(ApiError):
        client.file(9, timeout_s=0)
    assert len(script.hits) == 1
