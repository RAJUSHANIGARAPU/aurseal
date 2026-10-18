"""The small HTTP helper the GitHub adapters share: token header, pagination, rate limits."""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import requests

API_URL_ENV = "GITHUB_API_URL"
DEFAULT_API = "https://api.github.com"
TIMEOUT_S = 20


class ServiceUnavailable(RuntimeError):
    """The service could not answer (network, auth, rate limit): no data, not "empty"."""


class GitHubClient:
    def __init__(self, token: str | None = None, session: requests.Session | None = None) -> None:
        self.base = (os.environ.get(API_URL_ENV) or DEFAULT_API).rstrip("/")
        self.session = session or requests.Session()
        self.headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:
            self.headers["Authorization"] = f"Bearer {token}"

    def request(self, method: str, path: str, **kw: Any) -> requests.Response:
        url = path if path.startswith("http") else f"{self.base}{path}"
        try:
            response = self.session.request(
                method, url, headers=self.headers, timeout=TIMEOUT_S, **kw
            )
        except requests.RequestException as exc:
            raise ServiceUnavailable(f"{method} {path}: {exc}") from exc
        limited = response.status_code == 429 or (
            response.status_code == 403 and response.headers.get("X-RateLimit-Remaining") == "0"
        )
        if limited:
            raise ServiceUnavailable(f"{method} {path}: rate limited")
        return response

    def json(self, method: str, path: str, **kw: Any) -> Any:
        response = self.request(method, path, **kw)
        if response.status_code >= 400:
            raise ServiceUnavailable(f"{method} {path}: HTTP {response.status_code}")
        return response.json()

    def paginate(self, path: str, params: dict[str, Any]) -> Iterator[dict[str, Any]]:
        page = 1
        while True:
            batch = self.json("GET", path, params={**params, "per_page": 100, "page": page})
            if not isinstance(batch, list):
                raise ServiceUnavailable(f"GET {path}: expected a list")
            yield from batch
            if len(batch) < 100:
                return
            page += 1
