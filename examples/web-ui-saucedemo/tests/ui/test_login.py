"""Sign-in outcomes for every user class the site documents."""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect

from web_ui_saucedemo.data.users import LOGIN_CASES, LoginCase
from web_ui_saucedemo.pages.inventory_page import InventoryPage
from web_ui_saucedemo.pages.login_page import LoginPage

pytestmark = pytest.mark.regression


@pytest.mark.smoke
def test_standard_user_lands_on_the_product_list(page: Page):
    LoginPage(page).open().sign_in("standard_user", "secret_sauce")
    expect(InventoryPage(page).title).to_have_text("Products")


@pytest.mark.parametrize("case", LOGIN_CASES, ids=lambda case: case.id)
def test_sign_in_outcome_matches_the_user(page: Page, case: LoginCase):
    login = LoginPage(page).open().sign_in(case.username, case.password)

    if case.signed_in:
        expect(page).to_have_url(re.compile(re.escape(InventoryPage.path) + "$"))
        expect(InventoryPage(page).title).to_have_text("Products")
    else:
        expect(login.error).to_contain_text(case.error_contains)
        expect(login.username).to_be_visible()  # still on the sign-in page


def test_product_list_is_closed_to_a_visitor_who_has_not_signed_in(page: Page):
    InventoryPage(page).open()
    expect(LoginPage(page).error).to_contain_text("when you are logged in")
