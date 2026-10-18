"""A subscription seeded to just after plan selection, opened straight on its Price step."""

from __future__ import annotations

from decimal import Decimal

import pytest

from tests.demo_examples.ui import support
from ui.pages.demo.price_step import PriceStep

pytestmark = pytest.mark.demo_ui


def test_deep_link_to_the_price_step(demo_page, demo_sut, demo_control, demo_seed):
    demo_control.freeze(support.NOW)
    # the shorter fixture: parties and plan chosen, nothing priced yet (status draft)
    sub = support.segment_subscription(demo_seed, "company", state="draft")
    assert sub["status"] == "draft"

    price = PriceStep(demo_page, demo_sut.base_url).open(sub["id"])
    assert demo_page.url.endswith(f"/ui/subscriptions/{sub['id']}/step/quote")
    assert price.current_step() == "Price"
    assert price.raw_value("net") == "32.50" and price.raw_value("tax_rate") == "20"

    outcome = price.calculate()
    assert outcome.status == 200, outcome.describe()
    assert price.amounts() == {
        "net": Decimal("32.50"),
        "tax": Decimal("6.50"),
        "gross": Decimal("39.00"),
        "commission": Decimal("3.25"),
    }
    stored = support.subscription(demo_sut.client, sub["id"])["price"]
    assert support.money(stored["monthly_gross"]) == price.amount("gross")
