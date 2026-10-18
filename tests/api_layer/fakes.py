"""An offline transport for ``requests``: a mounted adapter that answers from a script."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import requests
from requests.adapters import BaseAdapter

Handler = Callable[[requests.PreparedRequest], "requests.Response | BaseException"]


def make_response(
    status: int, body: Any = None, headers: dict[str, str] | None = None, url: str = ""
) -> requests.Response:
    r = requests.Response()
    r.status_code = status
    if isinstance(body, bytes):
        r._content = body
    elif body is None:
        r._content = b""
    else:
        r._content = json.dumps(body).encode()
    r.headers.update(headers or {})
    r.url = url
    return r


@dataclass
class FakeAdapter(BaseAdapter):
    """Answers each request with the first handler whose (method, path-substring) matches."""

    routes: list[tuple[str, str, Handler]] = field(default_factory=list)
    sent: list[requests.PreparedRequest] = field(default_factory=list)

    def __post_init__(self) -> None:
        super().__init__()

    def on(self, method: str, path_part: str, handler: Handler | requests.Response) -> FakeAdapter:
        if isinstance(handler, requests.Response):
            fixed = handler
            self.routes.append((method, path_part, lambda _r: fixed))
        else:
            self.routes.append((method, path_part, handler))
        return self

    def send(self, request: requests.PreparedRequest, **kwargs: Any) -> requests.Response:
        self.sent.append(request)
        for method, part, handler in self.routes:
            if request.method == method and part in request.url:
                result = handler(request)
                if isinstance(result, BaseException):
                    raise result
                result.url = request.url
                return result
        return make_response(404, {"error": "no route"}, url=request.url)

    def close(self) -> None:
        pass


def session_with(adapter: FakeAdapter) -> requests.Session:
    s = requests.Session()
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    return s
