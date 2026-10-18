"""The product list shown after sign-in."""

from __future__ import annotations

from decimal import Decimal
from typing import Self

from playwright.sync_api import Locator

from ..components.header import Header
from ..money import dollars
from .base_page import BasePage


class InventoryPage(BasePage):
    path = "/inventory.html"

    @property
    def header(self) -> Header:
        return Header(self.page)

    @property
    def title(self) -> Locator:
        return self.page.get_by_test_id("title")

    @property
    def sort_order(self) -> Locator:
        return self.page.get_by_test_id("active-option")

    def item(self, name: str) -> Locator:
        return self.page.get_by_test_id("inventory-item").filter(
            has=self.page.get_by_test_id("inventory-item-name").get_by_text(name, exact=True)
        )

    def add_to_cart(self, name: str) -> Self:
        self.item(name).get_by_role("button", name="Add to cart").click()
        return self

    def remove(self, name: str) -> Self:
        self.item(name).get_by_role("button", name="Remove").click()
        return self

    def price_of(self, name: str) -> Decimal:
        return dollars(self.item(name).get_by_test_id("inventory-item-price").inner_text())

    def prices(self) -> list[Decimal]:
        texts = self.page.get_by_test_id("inventory-item-price").all_inner_texts()
        return [dollars(text) for text in texts]

    def sort_by(self, label: str) -> Self:
        self.page.get_by_test_id("product-sort-container").select_option(label=label)
        return self
