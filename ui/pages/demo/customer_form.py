"""
Customer maintenance (``/ui/customers/new``, ``/ui/customers/{id}/edit``).

One billing address block, addressed by field key (``line1``, ``postal_code``, ...); every
field is found by its exact test id, never by position.
"""

from __future__ import annotations

import re

from ui.components.dropdown import select_native
from ui.components.submit_outcome import SubmitResult, submit_and_capture
from ui.components.value_entry import fill_and_verify
from ui.pages.demo.base import DemoPage

ADDRESS_KEYS = ("line1", "line2", "postal_code", "city", "country")
SAVE_URL = re.compile(r"/api/customers(/\d+)?$")


class CustomerMaintenance(DemoPage):
    ready = "customer.form"

    # -- opening ----------------------------------------------------------------------------

    def open_new(self, start_route: str = "prospect") -> CustomerMaintenance:
        """Open the create form; a start-route chooser, when the app shows one, is answered."""
        self.page.goto(self.base_url + "/ui/customers/new")
        chooser = self.resolve("customer.start_route", route=start_route)
        if chooser.count():
            chooser.first.click()
        self.wait_ready()
        return self

    def open_edit(self, customer_id: int) -> CustomerMaintenance:
        self.goto(f"/ui/customers/{customer_id}/edit")
        self.resolve("customer.address_field", key="line1").wait_for()
        return self

    # -- identity -----------------------------------------------------------------------------

    def fill_identity(self, *, display_name: str, customer_type: str = "INDIVIDUAL") -> None:
        select_native(self.resolve("customer.type"), customer_type, by="value")
        fill_and_verify(self.resolve("customer.display_name"), display_name, description="name")

    # -- billing address ----------------------------------------------------------------------

    def address_field(self, key: str):
        if key not in ADDRESS_KEYS:
            raise KeyError(f"unknown address field {key!r}")
        return self.resolve("customer.address_field", key=key)

    def set_address(self, **values: str) -> dict[str, str]:
        for key, value in values.items():
            field = self.address_field(key)
            if value:
                fill_and_verify(field, value, description=f"address {key}")
            else:
                field.fill("")
        return self.read_address()

    def read_address(self) -> dict[str, str]:
        return {key: self.address_field(key).input_value() for key in ADDRESS_KEYS}

    # -- contact ------------------------------------------------------------------------------

    def set_email(self, email: str) -> str:
        field = self.resolve("customer.email")
        if not email:
            field.fill("")
            return field.input_value()
        return fill_and_verify(field, email, description="email")

    def read_email(self) -> str:
        return self.resolve("customer.email").input_value()

    def set_phone(self, phone: str) -> str:
        field = self.resolve("customer.phone")
        if not phone:
            field.fill("")
            return field.input_value()
        return fill_and_verify(field, phone, description="phone")

    def set_payout_account(self, account: str) -> str:
        return fill_and_verify(
            self.resolve("customer.payout_account"), account, description="payout account"
        )

    # -- saving ---------------------------------------------------------------------------------

    def save(self) -> SubmitResult:
        """Save; ``advanced`` = the form reported a saved number."""
        return submit_and_capture(
            self.page,
            lambda: self.resolve("customer.save").click(),
            SAVE_URL,
            method=None,
            advanced=lambda page: self.text("customer.save_status").startswith("Saved"),
            settle_ms=2000,
        )

    def saved_id(self) -> int:
        value = self.resolve("customer.save_status").get_attribute("data-customer-id")
        if not value:
            raise AssertionError(f"nothing saved yet: {self.text('customer.save_status')!r}")
        return int(value)

    def field_error(self, path: str) -> str:
        return self.text("customer.field_error", path=path)
