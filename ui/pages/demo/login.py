"""The demo's login form: a plain username/password form that sets a session cookie."""

from __future__ import annotations

from ui.components.value_entry import fill_and_verify
from ui.pages.demo.base import DemoPage


class DemoLoginPage(DemoPage):
    ready = "login.form"

    def open(self) -> DemoLoginPage:
        self.goto("/login")
        return self

    def login(self, username: str, password: str) -> None:
        fill_and_verify(self.resolve("login.username"), username, description="username")
        self.resolve("login.password").fill(password)
        with self.page.expect_navigation():
            self.resolve("login.submit").click()

    def on_login_page(self) -> bool:
        return self.page.url.split("?")[0].endswith("/login")

    def error(self) -> str:
        node = self.resolve("login.error")
        return node.inner_text().strip() if node.count() else ""

    def has_session_cookie(self) -> bool:
        return any(c["name"] == "demo_session" for c in self.page.context.cookies())
