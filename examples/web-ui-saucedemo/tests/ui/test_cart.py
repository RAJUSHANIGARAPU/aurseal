"""The cart badge follows what is in the cart."""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from web_ui_saucedemo.data.products import BACKPACK, BIKE_LIGHT, ONESIE
from web_ui_saucedemo.pages.inventory_page import InventoryPage

pytestmark = pytest.mark.regression


@pytest.mark.parametrize(
    "products",
    [[BACKPACK], [BACKPACK, BIKE_LIGHT], [BACKPACK, BIKE_LIGHT, ONESIE]],
    ids=["one-item", "two-items", "three-items"],
)
def test_cart_badge_counts_the_items_added(inventory: InventoryPage, products):
    for product in products:
        inventory.add_to_cart(product)
    expect(inventory.header.cart_badge).to_have_text(str(len(products)))


def test_cart_badge_count_matches_items_after_remove(inventory: InventoryPage):
    inventory.add_to_cart(BACKPACK).add_to_cart(BIKE_LIGHT).remove(BACKPACK)

    expect(inventory.header.cart_badge).to_have_text("1")
    cart = inventory.header.open_cart()
    expect(cart.items).to_have_count(1)
    expect(cart.items).to_contain_text(BIKE_LIGHT)


def test_cart_badge_disappears_when_the_cart_is_emptied(inventory: InventoryPage):
    inventory.add_to_cart(ONESIE).remove(ONESIE)
    expect(inventory.header.cart_badge).to_be_hidden()
