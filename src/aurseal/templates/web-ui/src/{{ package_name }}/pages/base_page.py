"""What every page object shares."""

from __future__ import annotations

from typing import Self

from playwright.sync_api import Page


class BasePage:
    """A page reached at ``path`` relative to the context's base URL.

    Page objects locate and act; they never assert. Assertions stay in the tests,
    where a reader expects them.
    """

    path = "/"

    def __init__(self, page: Page) -> None:
        self.page = page

    def open(self) -> Self:
        self.page.goto(self.path)
        return self
