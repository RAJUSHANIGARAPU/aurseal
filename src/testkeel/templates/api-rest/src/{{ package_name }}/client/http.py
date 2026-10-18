"""A thin client over httpx: timeouts, a retry policy for GET only, redacted logging."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from types import TracebackType
from typing import Any, Self

import httpx

log = logging.getLogger(__name__)

#: Repeating these is safe: the server state cannot change because we asked twice.
RETRYABLE_METHODS = frozenset({"GET"})
#: Worth another attempt: the server or a proxy says "not now", not "no".
RETRYABLE_STATUSES = frozenset({429, 502, 503, 504})
SENSITIVE_HEADERS = frozenset({"authorization", "cookie", "set-cookie", "x-api-key"})


def redact(headers: Mapping[str, str]) -> dict[str, str]:
    """Headers safe to log: credentials replaced by ``***``."""
    return {k: "***" if k.lower() in SENSITIVE_HEADERS else v for k, v in headers.items()}


class ApiClient:
    """Every request goes through ``request``; services never touch httpx directly."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_s: float = 10.0,
        max_retries: int = 2,
        backoff_s: float = 0.5,
        headers: Mapping[str, str] | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.max_retries = max_retries
        self.backoff_s = backoff_s
        self._sleep = sleep
        self._http = httpx.Client(
            base_url=base_url,
            timeout=timeout_s,
            headers=dict(headers or {}),
            transport=transport,
        )

    def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        method = method.upper()
        attempts = 1 + (self.max_retries if method in RETRYABLE_METHODS else 0)
        for attempt in range(1, attempts + 1):
            last = attempt == attempts
            try:
                response = self._http.request(method, path, **kwargs)
            except httpx.TransportError as error:
                if last:
                    raise
                log.warning("%s %s failed (%s); retrying", method, path, error)
                self._sleep(self._delay(attempt, None))
                continue
            self._log(response)
            if last or response.status_code not in RETRYABLE_STATUSES:
                return response
            self._sleep(self._delay(attempt, response))
        raise AssertionError("unreachable: the last attempt always returns or raises")

    def _delay(self, attempt: int, response: httpx.Response | None) -> float:
        """Exponential backoff, unless the server said how long to wait."""
        retry_after = response.headers.get("Retry-After") if response is not None else None
        if retry_after and retry_after.isdigit():
            return float(retry_after)
        return float(self.backoff_s * 2 ** (attempt - 1))

    @staticmethod
    def _log(response: httpx.Response) -> None:
        request = response.request
        log.info(
            "%s %s -> %s headers=%s",
            request.method,
            request.url,
            response.status_code,
            redact(request.headers),
        )

    def get(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", path, **kwargs)

    def put(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("PUT", path, **kwargs)

    def patch(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("PATCH", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("DELETE", path, **kwargs)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
