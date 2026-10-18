"""Customer maintenance through its page object: create a customer, edit it, read it back."""

from __future__ import annotations

import pytest

from demo_sut.customers import make_iban
from ui.pages.demo.customer_form import CustomerMaintenance

pytestmark = pytest.mark.demo_ui


def test_create_edit_and_read_back_a_customer(demo_page, demo_sut, demo_control):
    account = make_iban(bban="UIFORM0000017")
    form = CustomerMaintenance(demo_page, demo_sut.base_url).open_new()
    form.fill_identity(display_name="Harbourline Studio", customer_type="COMPANY")
    first = form.set_address(line1="2 Pier Steps", postal_code="PS 12", city="Gullhaven")
    assert first["country"] == "XA", "the home country is preset"
    form.set_email("studio@harbourline.example")
    form.set_phone("+31 20 555 0199")
    form.set_payout_account(account)
    created = form.save()
    assert created.status == 201 and created.advanced, created.describe()
    customer_id = form.saved_id()

    # reopen: every value reads back from the stored record
    form.open_edit(customer_id)
    assert form.read_address() == {
        "line1": "2 Pier Steps",
        "line2": "",
        "postal_code": "PS 12",
        "city": "Gullhaven",
        "country": "XA",
    }
    assert form.read_email() == "studio@harbourline.example"

    # edit one address field and clear the phone, then read back again
    form.set_address(line2="Second floor")
    form.set_phone("")
    edited = form.save()
    assert edited.status == 200 and edited.advanced, edited.describe()
    form.open_edit(customer_id)
    assert form.read_address()["line2"] == "Second floor"
    assert form.read_address()["line1"] == "2 Pier Steps", "editing one field changed another"

    stored = demo_sut.client.ok("GET", f"/api/customers/{customer_id}")
    assert stored["type"] == "COMPANY" and stored["phone"] is None
    assert stored["payout_account"] == account
