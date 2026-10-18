"""Run the demo on a localhost port, in the foreground or in a background thread."""

from __future__ import annotations

import socketserver
import tempfile
import threading
from pathlib import Path
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from demo_sut.app import DemoApp


class _QuietHandler(WSGIRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - stdlib name
        return


class _ThreadingServer(socketserver.ThreadingMixIn, WSGIServer):
    daemon_threads = True
    allow_reuse_address = True


class DemoServer:
    """A demo instance on ``host:port`` (port 0 picks a free port) with its own database.

    ``db_path=None`` creates a fresh database file in a new temporary directory.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 0,
        db_path: str | Path | None = None,
        quiet: bool = True,
    ) -> None:
        if db_path is None:
            self._tmp = tempfile.TemporaryDirectory(prefix="demo_sut_")
            db_path = Path(self._tmp.name) / "demo.sqlite"
        else:
            self._tmp = None
        self.db_path = Path(db_path)
        self.app = DemoApp(self.db_path)
        handler = _QuietHandler if quiet else WSGIRequestHandler
        self.httpd = make_server(
            host, port, self.app, server_class=_ThreadingServer, handler_class=handler
        )
        self.host = host
        self.port = self.httpd.server_port
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self) -> DemoServer:
        self._thread = threading.Thread(
            target=self.httpd.serve_forever, name="demo-sut", daemon=True
        )
        self._thread.start()
        return self

    def serve_forever(self) -> None:
        self.httpd.serve_forever()

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        if self._thread:
            self._thread.join(timeout=5)
        self.app.close()
        if self._tmp is not None:
            self._tmp.cleanup()

    def __enter__(self) -> DemoServer:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()
