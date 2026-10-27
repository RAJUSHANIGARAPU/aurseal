"""Tracker reads through a transport that can only GET one host's allowed paths.

Status lookups and requirement syncs read a shared tracker that other people work in. The
transport they get cannot write to it: :class:`ReadOnlyTrackerTransport` has one public
method, ``get``, and refuses, before any byte is sent,

* an absolute URL whose host is not the configured one;
* a path outside the allowed prefixes (a prefix matches whole path segments, and a path
  with ``..`` in it is refused outright).

It keeps a closure over the session's ``get`` and nothing else, so there is no session
attribute to reach through for a ``post``. Filing a drafted issue is a separate, explicit
step that builds its own full client (``integrations.trackers.github``).

``read_json`` and ``paginate`` add the GitHub answer handling (rate limit, error status,
page walking) on top of the transport.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from typing import Any
from urllib.parse import urlsplit

import requests

from integrations.http import API_URL_ENV, DEFAULT_API, TIMEOUT_S, ServiceUnavailable


class TransportRefused(PermissionError):
    """A request the read-only transport will not send (wrong host, path or verb)."""


def _segments_prefix(path: str, prefix: str) -> bool:
    prefix = "/" + prefix.strip("/")
    return path == prefix or path.startswith(prefix + "/")


class ReadOnlyTrackerTransport:
    """GET only, to ``base_url``'s host, under ``allowed_paths``."""

    __slots__ = ("_get", "allowed_paths", "base_url", "host")

    def __init__(
        self,
        base_url: str,
        allowed_paths: Iterable[str],
        session: Any = None,
        *,
        headers: Mapping[str, str] | None = None,
        timeout_s: float = TIMEOUT_S,
    ) -> None:
        parts = urlsplit(base_url)
        if not parts.scheme or not parts.hostname:
            raise ValueError(f"the transport needs an absolute base URL, got {base_url!r}")
        allowed = tuple(p for p in allowed_paths if p and p.strip("/"))
        if not allowed:
            raise ValueError("the transport needs at least one allowed path")
        fixed = dict(headers or {})
        session_get = (session or requests.Session()).get

        def _get(url: str, params: Mapping[str, Any] | None) -> Any:
            return session_get(url, headers=fixed, params=params, timeout=timeout_s)

        object.__setattr__(self, "base_url", base_url.rstrip("/"))
        object.__setattr__(self, "host", parts.hostname.lower())
        object.__setattr__(self, "allowed_paths", allowed)
        object.__setattr__(self, "_get", _get)

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("ReadOnlyTrackerTransport is immutable")

    def _url(self, path: str) -> str:
        if path.startswith(("http://", "https://")):
            parts = urlsplit(path)
            if (parts.hostname or "").lower() != self.host:
                raise TransportRefused(
                    f"refused: {parts.hostname!r} is not the configured tracker host {self.host!r}"
                )
            target, url = parts.path, path
        elif "://" in path or path.startswith("//"):
            raise TransportRefused(f"refused: {path!r} is not a path on the tracker")
        else:
            target = "/" + path.lstrip("/")
            url = self.base_url + target
        base_path = urlsplit(self.base_url).path.rstrip("/")
        relative = (
            target[len(base_path) :] if base_path and target.startswith(base_path) else target
        )
        if ".." in relative.split("/") or not any(
            _segments_prefix(relative, p) for p in self.allowed_paths
        ):
            raise TransportRefused(
                f"refused: {relative!r} is outside the allowed paths {list(self.allowed_paths)}"
            )
        return url

    def get(self, path: str, params: Mapping[str, Any] | None = None) -> Any:
        """GET ``path`` (relative to the base URL, or absolute on the same host)."""
        return self._get(self._url(path), params)


def github_transport(
    allowed_paths: Iterable[str], token: str | None = None, session: Any = None
) -> ReadOnlyTrackerTransport:
    """A read-only transport for the GitHub REST API (``GITHUB_API_URL`` honoured)."""
    import os

    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    base = (os.environ.get(API_URL_ENV) or DEFAULT_API).rstrip("/")
    return ReadOnlyTrackerTransport(base, allowed_paths, session, headers=headers)


def fetch(transport: ReadOnlyTrackerTransport, path: str, params: Mapping[str, Any] | None = None):
    """The response, or ``ServiceUnavailable`` for no answer or a rate limit."""
    try:
        response = transport.get(path, params)
    except requests.RequestException as exc:
        raise ServiceUnavailable(f"GET {path}: {exc}") from exc
    limited = response.status_code == 429 or (
        response.status_code == 403 and response.headers.get("X-RateLimit-Remaining") == "0"
    )
    if limited:
        raise ServiceUnavailable(f"GET {path}: rate limited")
    return response


def read_json(
    transport: ReadOnlyTrackerTransport, path: str, params: Mapping[str, Any] | None = None
) -> Any:
    response = fetch(transport, path, params)
    if response.status_code >= 400:
        raise ServiceUnavailable(f"GET {path}: HTTP {response.status_code}")
    return response.json()


def paginate(
    transport: ReadOnlyTrackerTransport, path: str, params: Mapping[str, Any]
) -> Iterator[dict[str, Any]]:
    page = 1
    while True:
        batch = read_json(transport, path, {**params, "per_page": 100, "page": page})
        if not isinstance(batch, list):
            raise ServiceUnavailable(f"GET {path}: expected a list")
        yield from batch
        if len(batch) < 100:
            return
        page += 1
