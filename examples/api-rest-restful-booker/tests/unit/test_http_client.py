"""The client's retry policy and logging, on httpx.MockTransport: no network, no waiting."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator

import httpx
import pytest

from api_rest_restful_booker.client import ApiClient

pytestmark = pytest.mark.offline

Handler = Callable[[httpx.Request], httpx.Response]


def scripted(*steps: int | Exception) -> tuple[Handler, list[httpx.Request]]:
    """A handler that answers each request with the next status (or raises the next error)."""
    queue: Iterator[int | Exception] = iter(steps)
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        step = next(queue)
        if isinstance(step, Exception):
            raise step
        headers = {"Retry-After": "3"} if step == 429 else {}
        return httpx.Response(step, headers=headers, json={})

    return handle, seen


def client_for(handler: Handler, delays: list[float], **kwargs) -> ApiClient:
    return ApiClient(
        "https://api.example.test",
        transport=httpx.MockTransport(handler),
        sleep=delays.append,
        **kwargs,
    )


def test_get_is_retried_after_server_errors_with_growing_backoff():
    handler, seen = scripted(503, 502, 200)
    delays: list[float] = []

    response = client_for(handler, delays, max_retries=2, backoff_s=0.5).get("/items")

    assert response.status_code == 200
    assert len(seen) == 3
    assert delays == [0.5, 1.0]


def test_get_gives_up_after_max_retries_and_returns_the_last_answer():
    handler, seen = scripted(503, 503, 503)

    response = client_for(handler, [], max_retries=2).get("/items")

    assert response.status_code == 503
    assert len(seen) == 3


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_a_write_is_never_retried(method):
    handler, seen = scripted(503)

    response = client_for(handler, [], max_retries=3).request(method, "/items")

    assert response.status_code == 503
    assert len(seen) == 1


@pytest.mark.parametrize("status", [400, 401, 404, 409, 500])
def test_get_is_not_retried_on_an_answer_that_will_not_change(status):
    handler, seen = scripted(status)

    assert client_for(handler, []).get("/items").status_code == status
    assert len(seen) == 1


def test_retry_after_overrides_the_backoff():
    handler, _ = scripted(429, 200)
    delays: list[float] = []

    client_for(handler, delays, backoff_s=0.1).get("/items")

    assert delays == [3.0]


def test_get_is_retried_after_a_connection_error():
    handler, seen = scripted(httpx.ConnectError("refused"), 200)

    assert client_for(handler, []).get("/items").status_code == 200
    assert len(seen) == 2


def test_a_connection_error_on_the_last_attempt_is_raised():
    handler, _ = scripted(httpx.ConnectError("refused"))

    with pytest.raises(httpx.ConnectError):
        client_for(handler, [], max_retries=0).get("/items")


def test_credentials_never_reach_the_log(caplog):
    handler, _ = scripted(200)
    client = client_for(handler, [], headers={"Authorization": "Bearer s3cret-token"})

    with caplog.at_level(logging.INFO):
        client.get("/items")

    assert "s3cret-token" not in caplog.text
    assert "***" in caplog.text
