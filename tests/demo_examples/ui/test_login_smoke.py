"""Standalone login smoke: log in through the form, leave the login page, use the session."""

from __future__ import annotations

import pytest

from ui.pages.demo.login import DemoLoginPage
from ui.pages.demo.subscription_wizard import SubscriptionWizard

pytestmark = pytest.mark.demo_ui

# throwaway credentials of the local demo (see demo_sut/README.md, Settings)
USER, PASSWORD = "demo-clerk", "demo-password"


def test_login_smoke_preset(demo_page, demo_sut, demo_control):
    demo_control.settings(auth_required=True)
    login = DemoLoginPage(demo_page, demo_sut.base_url)

    # control: without a session the screen's API call is refused and the banner says so
    wizard = SubscriptionWizard(demo_page, demo_sut.base_url).open()
    with demo_page.expect_response(lambda r: r.url.endswith("/api/search")) as refused:
        wizard.open_customer_picker()
    assert refused.value.status == 401
    assert wizard.error_banner().startswith("Request failed (401)")

    login.open().login(USER, "not-the-password")
    assert login.on_login_page() and login.error() == "Invalid username or password."
    assert not login.has_session_cookie()

    login.open().login(USER, PASSWORD)
    assert not login.on_login_page() and demo_page.url.endswith("/ui"), demo_page.url
    assert login.has_session_cookie()
    wizard.open()
    with demo_page.expect_response(lambda r: r.url.endswith("/api/search")) as allowed:
        wizard.open_customer_picker()
    assert allowed.value.status == 200
    assert wizard.resolve("wizard.pick_partner").is_visible()
