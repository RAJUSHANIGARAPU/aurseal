"""Fault injection for API calls: a ``requests``-compatible session that misbehaves on cue.

``FaultyTransport(inner, specs, seed=0)`` has the ``request(method, url, **kw)`` method
``HttpEngine`` calls, so it drops in through ``HttpEngine(base_url, session=...)``:

* ``status``  — no request is sent; a response with the spec's status and
  ``x-fault-injected`` is returned;
* ``abort``   — raises ``requests.ConnectionError`` (connection reset);
* ``timeout`` — raises ``requests.Timeout``;
* ``latency`` — waits ``latency_ms``, then sends the real request;
* ``truncate_body`` — sends the real request and returns half its body.

Every fired fault is counted on ``.plan``; ``remove()`` turns injection off.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from typing import Any

import requests

from checks.faults.spec import HEADER, FaultKind, FaultPlan, FaultSpec


def _response(url: str, status: int, body: bytes, headers: dict[str, str]) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response._content = body
    response.url = url
    response.headers.update(headers)
    response.reason = "Injected fault"
    return response


class FaultyTransport:
    def __init__(
        self,
        inner: Any,
        specs: Sequence[FaultSpec],
        *,
        seed: int = 0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.inner = inner
        self.plan = FaultPlan(tuple(specs), seed=seed)
        self._sleep = sleep

    def fired(self, spec_id: str | None = None) -> int:
        return self.plan.fired(spec_id)

    def remove(self) -> None:
        self.plan.active = False

    def request(self, method: str, url: str, **kw: Any) -> requests.Response:
        spec = self.plan.decide(method, url)
        if spec is None:
            return self.inner.request(method, url, **kw)
        if spec.kind is FaultKind.STATUS:
            return _response(
                url,
                spec.status,
                b'{"error": "fault_injected"}',
                {HEADER: spec.id, "Content-Type": "application/json"},
            )
        if spec.kind is FaultKind.ABORT:
            raise requests.ConnectionError(f"connection reset (injected: {spec.id})")
        if spec.kind is FaultKind.TIMEOUT:
            raise requests.Timeout(f"timed out (injected: {spec.id})")
        if spec.kind is FaultKind.LATENCY:
            self._sleep(spec.latency_ms / 1000.0)
            response = self.inner.request(method, url, **kw)
            response.headers[HEADER] = spec.id
            return response
        # truncate_body
        real = self.inner.request(method, url, **kw)
        content = real.content
        return _response(
            url, real.status_code, content[: len(content) // 2], {**real.headers, HEADER: spec.id}
        )

    def __getattr__(self, name: str) -> Any:
        # Anything else a session offers (headers, close, mount...) is the inner session's.
        return getattr(self.inner, name)
