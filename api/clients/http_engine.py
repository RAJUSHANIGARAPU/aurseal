"""One HTTP transport for every API call a test makes, with a typed response wrapper.

``HttpEngine`` owns the things that must be identical on every request and so
must not be re-implemented per client: base URL joining, auth, run tagging
(:mod:`api.clients.run_tagging`), timeouts, multipart upload, binary download,
version-guarded retry (:mod:`api.clients.versioned_retry`) and response
observers — callbacks that see every response, which is how the cleanup ledger
(:mod:`test_data.cleanup`) learns about entities a test created without each
test having to register them by hand.

``ApiResponse`` is immutable and carries status, raw body and headers, so an
assertion message can always show what came back.
"""

from __future__ import annotations

import json as _json
import logging
import os
import re
import time
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin

import requests
from requests.structures import CaseInsensitiveDict

from api.clients import run_tagging, versioned_retry

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = float(os.environ.get("API_TIMEOUT_S", "60"))
BODY_LOG_LIMIT = 800

_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


@dataclass(frozen=True)
class ApiResponse:
    """Status, raw body and headers of one response. Header lookup is case-insensitive."""

    status: int
    content: bytes = b""
    headers: Mapping[str, str] = field(default_factory=dict)
    url: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "headers", CaseInsensitiveDict(self.headers))

    @property
    def is_success(self) -> bool:
        return 200 <= self.status < 300

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def json(self) -> Any:
        """The parsed body; raises ``ValueError`` when it is not JSON."""
        if not self.content.strip():
            raise ValueError(f"HTTP {self.status} has an empty body")
        return _json.loads(self.content)

    def header(self, name: str, default: str | None = None) -> str | None:
        return self.headers.get(name, default)

    @property
    def location(self) -> str | None:
        return self.header("Location")

    @property
    def location_id(self) -> str | None:
        """The id a create returned in ``Location``: its last UUID, else its last path segment."""
        loc = self.location
        if not loc:
            return None
        uuids = _UUID.findall(loc)
        if uuids:
            return uuids[-1]
        tail = loc.split("?")[0].rstrip("/").rsplit("/", 1)[-1]
        return tail or None

    def raise_for_status(self) -> ApiResponse:
        if not self.is_success:
            raise ApiError(self)
        return self

    def __str__(self) -> str:
        body = self.text
        if len(body) > BODY_LOG_LIMIT:
            body = body[:BODY_LOG_LIMIT] + f"... ({len(self.content)} bytes)"
        return f"HTTP {self.status} {self.url} {body}"

    @classmethod
    def from_requests(cls, response: requests.Response) -> ApiResponse:
        return cls(response.status_code, response.content, dict(response.headers), response.url)


def _first(*values: float | None) -> float:
    return float(next(v for v in values if v is not None))


class ApiError(AssertionError):
    """A non-2xx response where a success was required; the message carries the response."""

    def __init__(self, response: ApiResponse) -> None:
        super().__init__(str(response))
        self.response = response


@dataclass(frozen=True)
class Part:
    """One part of a multipart/form-data request."""

    name: str
    content: bytes
    content_type: str
    filename: str | None = None

    @classmethod
    def json(cls, name: str, value: Any) -> Part:
        return cls(name, _json.dumps(value).encode("utf-8"), "application/json")

    @classmethod
    def file(cls, name: str, filename: str, content_type: str, content: bytes) -> Part:
        return cls(name, content, content_type, filename)


ResponseObserver = Callable[[str, str, ApiResponse], None]


class HttpEngine:
    """The single HTTP transport. Thread-safe for concurrent reads; one per test is cheapest."""

    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        headers: Mapping[str, str] | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        session: requests.Session | None = None,
        versioned: versioned_retry.Matcher | None = None,
        version_field: str = "version",
        max_retries: int = 2,
        backoff_s: float = 5.0,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/") + "/"
        self.timeout_s = timeout_s
        self._session = session or requests.Session()
        self._headers: dict[str, str] = {"Accept": "application/json"}
        if token:
            self._headers["Authorization"] = f"Bearer {token}"
        self._headers.update(headers or {})
        self._observers: list[ResponseObserver] = []
        # Opt-in runtime evidence for the coverage-gap report (ENDPOINT_HITS=true).
        from traceability.endpoint_hits import maybe_attach

        maybe_attach(self)
        self._versioned = versioned
        self._read_version = versioned_retry.version_reader(version_field)
        self._max_retries = max_retries
        self._backoff_s = backoff_s
        self._sleep = sleep

    # -- configuration ------------------------------------------------------

    def observe(self, observer: ResponseObserver) -> None:
        """Register a callback that sees ``(method, url, response)`` for every response."""
        self._observers.append(observer)

    def url(self, path: str) -> str:
        if path.startswith(("http://", "https://")):
            return path
        return urljoin(self.base_url, path.lstrip("/"))

    # -- verbs --------------------------------------------------------------

    def get(self, path: str, params: Mapping[str, Any] | None = None, **kw: Any) -> ApiResponse:
        return self.request("GET", path, params=params, **kw)

    def post(self, path: str, json: Any = None, **kw: Any) -> ApiResponse:
        return self.request("POST", path, json=json, **kw)

    def put(self, path: str, json: Any = None, **kw: Any) -> ApiResponse:
        return self.request("PUT", path, json=json, **kw)

    def patch(self, path: str, json: Any = None, **kw: Any) -> ApiResponse:
        return self.request("PATCH", path, json=json, **kw)

    def delete(self, path: str, **kw: Any) -> ApiResponse:
        return self.request("DELETE", path, **kw)

    def get_bytes(self, path: str, *, accept: str = "application/octet-stream") -> bytes:
        """Download a binary; raises :class:`ApiError` on a non-2xx."""
        return self.request("GET", path, headers={"Accept": accept}).raise_for_status().content

    def get_bytes_once_stored(
        self,
        path: str,
        *,
        timeout_s: float | None = None,
        interval_s: float | None = None,
        accept: str = "application/octet-stream",
        not_yet: Collection[int] = (404,),
        window_s: float | None = None,
        pause_s: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> bytes:
        """Download a file that may be listed before it reaches storage.

        A ``not_yet`` status (default: 404) before the deadline is logged and retried after
        ``interval_s``; any other non-2xx raises :class:`ApiError` at once, unretried.
        After ``timeout_s`` the last not-yet response is raised unchanged. The deadline is
        measured on a monotonic clock; an interrupt during the pause propagates.
        ``window_s``/``pause_s`` are accepted as aliases of ``timeout_s``/``interval_s``.
        """
        window = _first(timeout_s, window_s, 30.0)
        pause = _first(interval_s, pause_s, 1.0)
        if window < 0 or pause < 0:
            raise ValueError("timeout_s and interval_s must not be negative")
        sleep = self._sleep or time.sleep
        deadline = clock() + window
        attempt = 0
        while True:
            attempt += 1
            response = self.request("GET", path, headers={"Accept": accept})
            if response.is_success:
                return response.content
            if response.status not in not_yet:
                raise ApiError(response)
            remaining = deadline - clock()
            if remaining <= 0:
                raise ApiError(response)
            logger.info(
                "GET %s -> %d, not stored yet (attempt %d, %.1fs left)",
                path,
                response.status,
                attempt,
                remaining,
            )
            sleep(min(pause, remaining))

    def post_multipart(self, path: str, parts: list[Part]) -> ApiResponse:
        files = [(p.name, (p.filename, p.content, p.content_type)) for p in parts]
        return self.request("POST", path, files=files)

    # -- core ---------------------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        *,
        headers: Mapping[str, str] | None = None,
        **kw: Any,
    ) -> ApiResponse:
        method = method.upper()
        url = self.url(path)
        merged = {**run_tagging.request_headers(), **self._headers, **(headers or {})}

        def send() -> ApiResponse:
            return self._send(method, url, merged, kw)

        target = self._versioned(method, url) if self._versioned else None
        if target is None:
            response = send()
        else:
            response = versioned_retry.execute(
                target,
                send,
                lambda: self._send("GET", self.url(target.entity_path), merged, {}),
                read_version=self._read_version,
                max_retries=self._max_retries,
                backoff_s=self._backoff_s,
                **({"sleep": self._sleep} if self._sleep else {}),
            )
        for observer in self._observers:
            try:
                observer(method, url, response)
            except Exception:  # noqa: BLE001 - an observer must never fail the request
                logger.exception("response observer failed for %s %s", method, url)
        return response

    def _send(
        self, method: str, url: str, headers: Mapping[str, str], kw: dict[str, Any]
    ) -> ApiResponse:
        if "json" in kw and kw["json"] is not None:
            logger.debug("%s %s body=%s", method, url, _json.dumps(kw["json"])[:BODY_LOG_LIMIT])
        raw = self._session.request(
            method, url, headers=dict(headers), timeout=self.timeout_s, **kw
        )
        response = ApiResponse.from_requests(raw)
        logger.debug("%s %s -> %d", method, url, response.status)
        return response
