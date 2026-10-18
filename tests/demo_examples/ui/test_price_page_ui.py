"""The price step's page object: inputs, dropdowns, read-only detection, scoped reads."""

from __future__ import annotations

from decimal import Decimal

import pytest

from demo_sut.billing import PRICE_MESSAGES
from tests.demo_examples.ui import support
from ui.components.exact_attribute import by_path
from ui.components.field_reader import read_field
from ui.pages.demo.draft_screen import DraftScreen
from ui.pages.demo.price_step import PriceStep
from ui.pages.demo.subscription_wizard import SubscriptionWizard

pytestmark = pytest.mark.demo_ui


def test_price_inputs_and_dropdowns(demo_page, demo_sut, demo_control, demo_seed):
    demo_control.freeze(support.NOW)
    # dropdowns live on the Parties step: native selects, chosen and read back
    wizard = SubscriptionWizard(demo_page, demo_sut.base_url).open()
    assert wizard.choose_frequency("Yearly") == "Yearly"
    assert wizard.choose_plan("Individual plan (INDIVIDUAL)") == "Individual plan (INDIVIDUAL)"

    sub = support.segment_subscription(demo_seed, "company", state="draft")
    price = PriceStep(demo_page, demo_sut.base_url).open(sub["id"])
    assert price.raw_value("net") == "32.50" and price.raw_value("tax_rate") == "20"
    assert price.enter("net", "57.25") == "57.25"
    assert price.clear("net") == ""
    assert price.enter_literal("net", "abc") == "abc", "the field filtered a literal"
    assert price.calculate().rejected
    for name in ("net", "tax_rate", "commission"):
        assert price.is_read_only(name) is False, f"{name} should be editable"

    # the frequency is fixed once the subscription exists: read-only on a change draft
    active = support.booked(demo_seed)
    draft = DraftScreen(demo_page, demo_sut.base_url).open(
        demo_seed.draft(active["id"], "change")["id"]
    )
    assert draft.frequency_read_only() is True


def test_scoped_amount_reads_errors_and_alerts(demo_page, demo_sut, demo_control, demo_seed):
    """The input and its computed twin share a name stem; only the exact path answers."""
    demo_control.freeze(support.NOW)
    sub = support.segment_subscription(demo_seed, "company", state="draft")
    price = PriceStep(demo_page, demo_sut.base_url).open(sub["id"])
    assert price.current_step() == "Price"
    price.enter("net", "45.50")
    assert price.calculate().status == 200

    assert by_path(demo_page, "price-net").count() == 1
    assert read_field(demo_page, path="price-net").raw == "45.50"
    assert price.amount("net") == Decimal("45.50")
    assert price.amount("tax") == Decimal("9.10") and price.amount("gross") == Decimal("54.60")
    assert price.field_errors() == {}

    price.enter("net", "0.50")
    price.enter("commission", "99")
    refused = price.calculate()
    assert refused.status == 422 and refused.rejected
    errors = price.field_errors()
    assert errors == {
        "net": PRICE_MESSAGES["minimum"],
        "commission": PRICE_MESSAGES["commission_cap"].format(cap=Decimal(20)),
    }
    assert len(refused.inline_errors) == 2, refused.describe()
    assert price.error_banner().startswith("Request failed (422)")
