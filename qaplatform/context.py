"""Per-request context (request id, client address) set by an adapter, read by the service.

A context variable rather than an argument on every service method: the HTTP adapter,
the UI and the CLI each set it once per call, and the service never has to trust a
caller to thread it through.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class RequestContext:
    request_id: str
    source_ip: str | None = None


_current: ContextVar[RequestContext | None] = ContextVar("qap_request", default=None)


def new_request_id() -> str:
    return "req_" + uuid.uuid4().hex[:16]


def current() -> RequestContext:
    ctx = _current.get()
    return ctx if ctx is not None else RequestContext(new_request_id())


@contextmanager
def request(
    request_id: str | None = None, source_ip: str | None = None
) -> Iterator[RequestContext]:
    ctx = RequestContext(request_id or new_request_id(), source_ip)
    token = _current.set(ctx)
    try:
        yield ctx
    finally:
        _current.reset(token)


__all__ = ["RequestContext", "current", "new_request_id", "request"]
