"""The item list: where an item created through the API should show up."""

from __future__ import annotations

from playwright.sync_api import Locator

from .base_page import BasePage


class ItemsPage(BasePage):
    path = "/items"

    def row(self, name: str) -> Locator:
        """The table row whose cell is exactly ``name``; matches nothing when it is not listed."""
        cell = self.page.get_by_role("cell", name=name, exact=True)
        return self.page.get_by_role("row").filter(has=cell)
