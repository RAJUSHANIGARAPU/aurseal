"""The header bar on every signed-in page."""

from __future__ import annotations

from playwright.sync_api import Locator, Page

from ..pages.cart_page import CartPage


class Header:
    def __init__(self, page: Page) -> None:
        self.page = page
        self.root = page.get_by_test_id("header-container")

    @property
    def cart_badge(self) -> Locator:
        return self.root.get_by_test_id("shopping-cart-badge")

    def open_cart(self) -> CartPage:
        self.root.get_by_test_id("shopping-cart-link").click()
        return CartPage(self.page)
