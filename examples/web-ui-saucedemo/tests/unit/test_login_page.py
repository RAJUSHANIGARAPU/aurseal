"""The sign-in page object against a local copy of the markup: needs Chromium, never the network.

When these fail, the page object drifted from the markup; when only the live tests fail,
the site changed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from web_ui_saucedemo.data.users import LOGIN_CASES, LoginCase
from web_ui_saucedemo.pages.inventory_page import InventoryPage
from web_ui_saucedemo.pages.login_page import LoginPage

pytestmark = pytest.mark.offline

FIXTURE = Path(__file__).parents[1] / "fixtures" / "static" / "login.html"


@pytest.fixture
def login(page: Page) -> LoginPage:
    page.set_content(FIXTURE.read_text(encoding="utf-8"))
    return LoginPage(page)


@pytest.mark.parametrize("case", LOGIN_CASES, ids=lambda case: case.id)
def test_sign_in_outcome_matches_the_case(login: LoginPage, case: LoginCase):
    login.sign_in(case.username, case.password)

    if case.signed_in:
        expect(InventoryPage(login.page).title).to_have_text("Products")
    else:
        expect(login.error).to_contain_text(case.error_contains)
