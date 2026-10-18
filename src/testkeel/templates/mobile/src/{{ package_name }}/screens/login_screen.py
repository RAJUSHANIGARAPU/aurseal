"""The login form. Ids are the demo app's; replace them with your app's."""

from __future__ import annotations

from typing import Self

from .base_screen import BaseScreen, Locator, accessibility_id, android_text


class LoginScreen(BaseScreen):
    marker = accessibility_id("Login-screen")
    login_form = accessibility_id("button-login-container")
    email = accessibility_id("input-email")
    password = accessibility_id("input-password")
    submit = accessibility_id("button-LOGIN")

    def sign_in(self, email: str, password: str) -> Self:
        self.tap(self.login_form)
        self.type_into(self.email, email)
        self.type_into(self.password, password)
        self.tap(self.submit)
        return self

    @staticmethod
    def error(fragment: str) -> Locator:
        """An inline validation message; the app gives these no accessibility id."""
        return android_text(fragment)

    def shows_error(self, fragment: str) -> bool:
        return self.is_visible(self.error(fragment))
