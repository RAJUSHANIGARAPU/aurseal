"""Live checks that hold for any healthy HTTP API."""

from __future__ import annotations

import uuid

import pytest

from api_rest_restful_booker.client import ApiClient

pytestmark = pytest.mark.regression


@pytest.mark.smoke
def test_api_answers_without_a_server_error(api: ApiClient):
    response = api.get("/")
    assert response.status_code < 500, response.text[:500]


def test_unknown_route_is_a_client_error_not_a_crash(api: ApiClient):
    response = api.get(f"/no-such-route-{uuid.uuid4().hex}")
    assert 400 <= response.status_code < 500, response.text[:500]
