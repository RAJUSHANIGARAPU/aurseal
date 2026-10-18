"""Integrations API: every service lists its optional parameters, and a base URL that points
at a private, loopback or non-https host is refused while a public https one is accepted."""

from __future__ import annotations

import pytest

UNSAFE = [
    "http://mail.example.test",
    "https://localhost/v1",
    "https://127.0.0.1/v1",
    "https://10.0.0.1/v1",
    "https://169.254.169.254/latest",
    "https://[::1]/v1",
    "/relative/path",
]


def test_optional_params_listed_and_base_url_validated(demo_http, demo_sut):
    services = demo_http.get("integrations/services").json()
    assert services, "no integration service is listed"
    for service in services:
        assert isinstance(service.get("optionalParams"), list), (
            f"{service['key']}: no optionalParams"
        )
    mailer = next(s for s in services if "baseUrl" in s["optionalParams"])
    assert "baseUrl" not in mailer["params"]  # optional, not required

    stored_before = demo_http.get("integrations").json()
    for url in UNSAFE:
        response = demo_http.post(
            f"integrations/{mailer['key']}", json={"apiKey": "k", "baseUrl": url}
        )
        assert response.status == 400, f"{url} was accepted: {response}"
        assert response.json()["field"] == "baseUrl"
    stored_after = demo_http.get("integrations").json()
    assert stored_after == stored_before, "a refused registration changed the stored integration"

    accepted = demo_http.post(
        f"integrations/{mailer['key']}",
        json={"apiKey": "k", "baseUrl": "https://mail.example.test/v1"},
    )
    assert accepted.status in (200, 201), accepted
    assert accepted.json()["baseUrl"] == "https://mail.example.test/v1"


@pytest.mark.parametrize("body", [{"baseUrl": "https://mail.example.test"}, {}])
def test_a_registration_without_its_required_params_is_refused(demo_http, body):
    services = demo_http.get("integrations/services").json()
    response = demo_http.post(f"integrations/{services[0]['key']}", json=body)
    assert response.status == 400 and "missing parameters" in response.json()["message"]
