"""The two checkout steps: buyer information, then the order overview."""

from __future__ import annotations

from decimal import Decimal

from playwright.sync_api import Locator

from ..money import dollars
from .base_page import BasePage


class CheckoutInformationPage(BasePage):
    path = "/checkout-step-one.html"

    def continue_with(self, first_name: str, last_name: str, postal_code: str) -> OverviewPage:
        self.page.get_by_test_id("firstName").fill(first_name)
        self.page.get_by_test_id("lastName").fill(last_name)
        self.page.get_by_test_id("postalCode").fill(postal_code)
        self.page.get_by_test_id("continue").click()
        return OverviewPage(self.page)


class OverviewPage(BasePage):
    path = "/checkout-step-two.html"

    @property
    def subtotal_label(self) -> Locator:
        return self.page.get_by_test_id("subtotal-label")

    @property
    def tax_label(self) -> Locator:
        return self.page.get_by_test_id("tax-label")

    @property
    def total_label(self) -> Locator:
        return self.page.get_by_test_id("total-label")

    def tax(self) -> Decimal:
        return dollars(self.tax_label.inner_text())

    def finish(self) -> Locator:
        """Places the order; returns the confirmation heading."""
        self.page.get_by_test_id("finish").click()
        return self.page.get_by_test_id("complete-header")
