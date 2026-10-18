"""Minimal WSGI request/response types and a route table (no framework)."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import Any
from urllib.parse import parse_qs

from demo_sut.core import BadRequest


@dataclass
class Request:
    method: str
    path: str
    query: dict[str, str]
    headers: dict[str, str]
    body: bytes = b""
    cookies: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_environ(cls, environ: dict[str, Any]) -> Request:
        length = int(environ.get("CONTENT_LENGTH") or 0)
        body = environ["wsgi.input"].read(length) if length else b""
        headers = {
            key[5:].replace("_", "-").lower(): value
            for key, value in environ.items()
            if key.startswith("HTTP_")
        }
        if environ.get("CONTENT_TYPE"):
            headers["content-type"] = environ["CONTENT_TYPE"]
        query = {k: v[0] for k, v in parse_qs(environ.get("QUERY_STRING", "")).items()}
        cookies = {}
        for part in headers.get("cookie", "").split(";"):
            if "=" in part:
                name, value = part.strip().split("=", 1)
                cookies[name] = value
        path = environ.get("PATH_INFO", "/") or "/"
        return cls(environ["REQUEST_METHOD"].upper(), path, query, headers, body, cookies)

    def json(self) -> dict[str, Any]:
        if not self.body:
            return {}
        try:
            value = json.loads(self.body)
        except ValueError as exc:
            raise BadRequest("request body is not valid JSON") from exc
        if not isinstance(value, dict):
            raise BadRequest("request body must be a JSON object")
        return value

    def form(self) -> dict[str, str]:
        return {k: v[0] for k, v in parse_qs(self.body.decode("utf-8", "replace")).items()}


@dataclass
class Response:
    status: int = 200
    body: bytes = b""
    headers: list[tuple[str, str]] = field(default_factory=list)

    @property
    def status_line(self) -> str:
        return f"{self.status} {HTTPStatus(self.status).phrase}"

    def header(self, name: str, value: str) -> Response:
        self.headers.append((name, value))
        return self


def json_response(data: Any, status: int = 200) -> Response:
    body = json.dumps(data, indent=None, sort_keys=False).encode()
    return Response(status, body, [("Content-Type", "application/json")])


def bytes_response(body: bytes, content_type: str, filename: str | None = None) -> Response:
    response = Response(200, body, [("Content-Type", content_type)])
    if filename:
        response.header("Content-Disposition", f'attachment; filename="{filename}"')
    return response


def html_response(body: str, status: int = 200) -> Response:
    return Response(status, body.encode(), [("Content-Type", "text/html; charset=utf-8")])


def redirect(location: str) -> Response:
    return Response(303, b"", [("Location", location)])


Handler = Callable[..., Response]


@dataclass
class Route:
    method: str
    path: str
    handler: Handler
    summary: str
    tag: str
    status: int = 200
    body: dict[str, Any] | None = None
    query: tuple[str, ...] = ()
    headers: tuple[str, ...] = ()
    produces: str = "application/json"
    api: bool = True
    regex: re.Pattern[str] = field(init=False)

    def __post_init__(self) -> None:
        pattern = re.sub(r"\{(\w+)\}", r"(?P<\1>[^/.]+)", self.path)
        self.regex = re.compile(f"^{pattern}$")


class Router:
    def __init__(self) -> None:
        self.routes: list[Route] = []

    def add(self, method: str, path: str, summary: str, tag: str, **kw: Any):
        def decorate(fn: Handler) -> Handler:
            self.routes.append(Route(method, path, fn, summary, tag, **kw))
            return fn

        return decorate

    def match(self, method: str, path: str) -> tuple[Route | None, dict[str, str], bool]:
        """Return (route, params, path_known). ``path_known`` is True if any method matched."""
        known = False
        for route in self.routes:
            found = route.regex.match(path)
            if not found:
                continue
            known = True
            if route.method == method:
                return route, found.groupdict(), True
        return None, {}, known
