"""Sorting the product list."""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from web_ui_saucedemo.pages.inventory_page import InventoryPage

pytestmark = pytest.mark.regression


@pytest.mark.parametrize(
    ("label", "descending"),
    [("Price (low to high)", False), ("Price (high to low)", True)],
    ids=["low-to-high", "high-to-low"],
)
def test_sort_by_price_orders_inventory(inventory: InventoryPage, label, descending):
    inventory.sort_by(label)
    expect(inventory.sort_order).to_have_text(label)

    prices = inventory.prices()
    assert prices == sorted(prices, reverse=descending)
