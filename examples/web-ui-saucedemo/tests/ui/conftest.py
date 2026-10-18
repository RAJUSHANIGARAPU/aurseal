"""Browser fixtures: the base URL and assertion timeout come from settings."""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from web_ui_saucedemo.data.users import PASSWORD
from web_ui_saucedemo.pages.inventory_page import InventoryPage
from web_ui_saucedemo.pages.login_page import LoginPage


@pytest.fixture(scope="session")
def base_url(settings):
    """Overrides the pytest-base-url fixture, so ``page.goto("/")`` goes to the target."""
    return settings.base_url


@pytest.fixture(scope="session", autouse=True)
def _expect_timeout(settings):
    expect.set_options(timeout=settings.expect_timeout_ms)


@pytest.fixture
def inventory(page):
    """A fresh context, signed in as the standard user, on the product list."""
    LoginPage(page).open().sign_in("standard_user", PASSWORD)
    shop = InventoryPage(page)
    expect(shop.title).to_have_text("Products")
    return shop
