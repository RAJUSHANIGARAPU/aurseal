"""An optional base-URL parameter renders as an unrequired field with a hint."""

from __future__ import annotations

import pytest

from ui.pages.demo.admin import IntegrationsScreen

pytestmark = pytest.mark.demo_ui


def test_optional_url_field_not_required(demo_page, demo_sut, demo_control):
    screen = IntegrationsScreen(demo_page, demo_sut.base_url).open()
    services = screen.services.latest
    assert screen.services.count >= 1 and services, "the services response was not captured"
    with_url = [s for s in services if "baseUrl" in s["optionalParams"]]
    assert with_url, "no service lists baseUrl as optional; nothing to check"
    for service in with_url:
        key = service["key"]
        assert "baseUrl" not in service["params"]
        field = screen.field(key, "baseUrl")
        assert field.visible and field.editable
        assert not field.required, f"{key}.baseUrl is optional but rendered required"
        assert not field.invalid
        assert "optional" in screen.label(key, "baseUrl").lower()
        assert screen.hint(key, "baseUrl").startswith("Optional")
        # control: a required parameter of the same form reads as required
        control = screen.field(key, service["params"][0])
        assert control.required, "the required control does not read as required"
