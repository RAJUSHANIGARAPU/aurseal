"""The Sauce Demo sign-in page. Every locator is a ``data-test`` id."""

from __future__ import annotations

from typing import Self

from playwright.sync_api import Locator

from .base_page import BasePage


class LoginPage(BasePage):
    path = "/"

    @property
    def username(self) -> Locator:
        return self.page.get_by_test_id("username")

    @property
    def password(self) -> Locator:
        return self.page.get_by_test_id("password")

    @property
    def error(self) -> Locator:
        return self.page.get_by_test_id("error")

    def sign_in(self, username: str, password: str) -> Self:
        self.username.fill(username)
        self.password.fill(password)
        self.page.get_by_test_id("login-button").click()
        return self
