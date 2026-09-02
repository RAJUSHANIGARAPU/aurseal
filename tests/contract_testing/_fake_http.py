"""
A stand-in for the network, shared by the capture tests.

``capture.install()`` records whatever ``requests.Session.send`` is bound to at
the moment it runs, so replacing ``send`` before installing puts the fake
underneath the patch: the patch is exercised exactly as it is in production and
nothing leaves the machine.
"""

from __future__ import annotations

from typing import Any

import requests

BASE = "http://api.test"


class FakeResponse:
    def __init__(self, status: int, payload: Any, headers: dict[str, str]) -> None:
        self.status_code = status
        self.headers = headers
        self._payload = payload

    def json(self) -> Any:
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeHttp:
    """Replaces ``requests.Session.send``. Counts calls, returns what it is told."""

    def __init__(self) -> None:
        self.status = 200
        self.payload: Any = {"ok": True}
        self.headers = {"content-type": "application/json"}
        self.sent = 0

    def send(self, session, request, **kwargs):  # noqa: ANN001, ARG002
        self.sent += 1
        return FakeResponse(self.status, self.payload, dict(self.headers))


def fire(path: str, method: str = "GET", body: Any = None) -> None:
    """Issue one request through the real requests stack onto the fake send."""
    prepared = requests.Request(method, f"{BASE}{path}", json=body).prepare()
    requests.Session().send(prepared)
