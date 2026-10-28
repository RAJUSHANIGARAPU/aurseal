"""A scripted stand-in for ``requests.Session``: routes by (method, path), records calls."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit


class FakeResponse:
    def __init__(self, status: int = 200, body: Any = None, headers: dict | None = None):
        self.status_code = status
        self._body = body
        self.headers = headers or {}

    def json(self) -> Any:
        return self._body


class FakeSession:
    def __init__(self, routes: dict[tuple[str, str], Any] | None = None):
        self.routes = dict(routes or {})
        self.calls: list[tuple[str, str, dict]] = []

    def get(self, url: str, **kw: Any) -> FakeResponse:
        return self.request("GET", url, **kw)

    def request(self, method: str, url: str, **kw: Any) -> FakeResponse:
        path = urlsplit(url).path
        self.calls.append((method, path, kw))
        handler = self.routes.get((method, path))
        if handler is None:
            return FakeResponse(404, {"message": "Not Found"})
        result = handler(kw) if callable(handler) else handler
        return result if isinstance(result, FakeResponse) else FakeResponse(200, result)
