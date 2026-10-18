"""Checkout arithmetic: the overview adds up, and the order goes through."""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from web_ui_saucedemo.data.products import BACKPACK, BIKE_LIGHT, FLEECE_JACKET
from web_ui_saucedemo.pages.inventory_page import InventoryPage

pytestmark = pytest.mark.regression


@pytest.mark.parametrize(
    "products",
    [[BACKPACK], [BACKPACK, BIKE_LIGHT], [BACKPACK, BIKE_LIGHT, FLEECE_JACKET]],
    ids=["one-item", "two-items", "three-items"],
)
def test_checkout_total_includes_tax_for_multiple_items(inventory: InventoryPage, products):
    expected_subtotal = sum(inventory.price_of(product) for product in products)
    for product in products:
        inventory.add_to_cart(product)

    overview = inventory.header.open_cart().checkout().continue_with("Ada", "Tester", "1011 AB")

    expect(overview.subtotal_label).to_have_text(f"Item total: ${expected_subtotal}")
    expect(overview.total_label).to_have_text(f"Total: ${expected_subtotal + overview.tax()}")


@pytest.mark.smoke
def test_order_is_confirmed_after_finish(inventory: InventoryPage):
    inventory.add_to_cart(BACKPACK)
    overview = inventory.header.open_cart().checkout().continue_with("Ada", "Tester", "1011 AB")
    expect(overview.finish()).to_have_text("Thank you for your order!")


@pytest.mark.xfail(reason="FINDING-1: any postal code is accepted", strict=True)
def test_a_postal_code_without_digits_is_refused(inventory: InventoryPage):
    inventory.add_to_cart(BACKPACK)
    inventory.header.open_cart().checkout().continue_with("Ada", "Tester", "ABC")
    expect(inventory.page.get_by_test_id("error")).to_be_visible(timeout=2_000)
