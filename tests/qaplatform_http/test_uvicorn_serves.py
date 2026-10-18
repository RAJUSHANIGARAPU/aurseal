"""The real thing: uvicorn on a loopback port, spoken to over HTTP by the Python client."""

from __future__ import annotations

import pytest
import requests


@pytest.fixture
def server(factory):
    from qaplatform.http.app import serve_in_thread

    with serve_in_thread(factory) as base_url:
        yield base_url


def test_served_over_a_socket(server, admin_token):
    from qaplatform.client import PlatformClient

    assert requests.get(f"{server}/api/v1/health", timeout=5).json() == {"status": "ok"}
    client = PlatformClient(server, admin_token, "default", timeout_s=5)
    assert client.health()["status"] == "ready"
    run = client.create_run("socket", expected_count=1)
    client.push_results(run["run_id"], [{"test_id": "t::socket", "outcome": "passed"}])
    assert client.finish_run(run["run_id"])["integrity"]["verdict"] == "PASS"
    page = requests.get(f"{server}/ui/login", timeout=5)
    assert page.status_code == 200 and "Sign in" in page.text
