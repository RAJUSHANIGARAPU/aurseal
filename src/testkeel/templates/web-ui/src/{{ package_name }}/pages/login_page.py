"""A sign-in form, located the way a user finds it: by label and role."""

from __future__ import annotations

from typing import Self

from playwright.sync_api import Locator

from .base_page import BasePage


class LoginPage(BasePage):
    path = "/login"

    @property
    def username(self) -> Locator:
        return self.page.get_by_label("Username")

    @property
    def password(self) -> Locator:
        return self.page.get_by_label("Password")

    @property
    def error(self) -> Locator:
        return self.page.get_by_role("alert")

    @property
    def welcome(self) -> Locator:
        return self.page.get_by_role("heading", name="Welcome")

    def sign_in(self, username: str, password: str) -> Self:
        self.username.fill(username)
        self.password.fill(password)
        self.page.get_by_role("button", name="Sign in").click()
        return self
