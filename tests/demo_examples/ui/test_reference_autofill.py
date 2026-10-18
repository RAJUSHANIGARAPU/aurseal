"""Choosing a reference record fills the fields derived from it; so does typing its number."""

from __future__ import annotations

import pytest

from tests.demo_examples.ui import support
from ui.pages.demo.subscription_wizard import SubscriptionWizard

pytestmark = pytest.mark.demo_ui


def expected_block(customer: dict) -> tuple[str, str]:
    address = customer["billing_address"]
    keys = ("line1", "line2", "postal_code", "city", "country")
    return address["country"], ", ".join(str(address[k]) for k in keys if address.get(k))


def test_reference_pick_and_direct_entry_autofill_the_address(
    demo_page, demo_sut, demo_control, demo_seed
):
    demo_control.freeze(support.NOW)
    customer = support.segment_customer(
        demo_seed,
        "company",
        billing_address={
            "line1": "Unit 9, Kiln Works",
            "line2": "Rope Walk",
            "postal_code": "KW 880",
            "city": "Ashbury",
            "country": "XB",
        },
    )
    wizard = SubscriptionWizard(demo_page, demo_sut.base_url).open()
    assert not wizard.address_block().filled, "the block is filled before anything was picked"

    # 1. pick in the reference dialog: the derived block equals the record the app fetched
    with demo_page.expect_response(
        lambda r: r.url.endswith(f"/api/customers/{customer['id']}")
    ) as caught:
        wizard.pick_customer(customer["number"])
    fetched = caught.value.json()
    block = wizard.address_block()
    assert (
        (block.country, block.text)
        == expected_block(fetched)
        == ("XB", "Unit 9, Kiln Works, Rope Walk, KW 880, Ashbury, XB")
    )
    assert wizard.change_offered("customer")

    # 2. direct identifier entry fills the same block, on a fresh screen
    wizard.open()
    typed = wizard.enter_customer_number(customer["number"])
    assert (typed.country, typed.text) == (block.country, block.text)
    assert wizard.last_customer_capture.count == 1

    # 3. an unknown number fills nothing and says so
    wizard.open()
    wizard.resolve("wizard.customer_number").fill("CUS-D1-999999")
    wizard.resolve("wizard.customer_number_lookup").click()
    wizard.resolve("wizard.customer_number_status").filter(has_text="No customer").wait_for()
    assert wizard.address_block().text == "-" and not wizard.address_block().filled
