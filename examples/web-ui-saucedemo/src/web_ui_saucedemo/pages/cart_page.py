"""The cart, reached from the header."""

from __future__ import annotations

from playwright.sync_api import Locator

from .base_page import BasePage
from .checkout_page import CheckoutInformationPage


class CartPage(BasePage):
    path = "/cart.html"

    @property
    def items(self) -> Locator:
        return self.page.get_by_test_id("inventory-item")

    def checkout(self) -> CheckoutInformationPage:
        self.page.get_by_test_id("checkout").click()
        return CheckoutInformationPage(self.page)
