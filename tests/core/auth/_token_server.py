"""A loopback OAuth token endpoint for grant tests: records every form it receives."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

Responder = Callable[[dict[str, str], dict[str, str]], tuple[int, dict]]


class TokenServer:
    def __init__(self, responder: Responder) -> None:
        self.forms: list[dict[str, str]] = []
        self.headers: list[dict[str, str]] = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def _answer(self, status: int, body: dict) -> None:
                payload = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_POST(self):  # noqa: N802 - http.server API
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length).decode()
                form = {k: v[0] for k, v in parse_qs(raw).items()}
                headers = {k.lower(): v for k, v in self.headers.items()}
                server.forms.append(form)
                server.headers.append(headers)
                self._answer(*responder(form, headers))

            def do_GET(self):  # noqa: N802 - http.server API
                auth = self.headers.get("Authorization", "")
                self._answer(200 if auth == "Bearer good" else 401, {})

            def log_message(self, *args):  # silence the default stderr logging
                pass

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}/oauth/token"

    def __enter__(self) -> TokenServer:
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
