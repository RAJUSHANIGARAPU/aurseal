"""
The wizard's Price step (``/ui/subscriptions/{id}/step/quote``) and its Confirm step.

Inputs are typed keystroke by keystroke and read back (``value_entry``), amounts
are read by exact test-id path (``field_reader``) so ``price-net`` never answers
with its ``price-net-normalised`` twin, and every calculation is captured as a
submit outcome: status, body, inline errors and alerts.
"""

from __future__ import annotations

import re
from decimal import Decimal

from ui.components.field_reader import field_is_read_only, read_field
from ui.components.submit_outcome import SubmitResult, submit_and_capture
from ui.components.value_entry import fill_amount_and_verify, fill_and_verify, parse_amount
from ui.pages.demo.base import DemoPage

QUOTE_URL = "/api/quotes/calculate"

#: input name -> (locator key, test-id path of the field, request field the API reports on)
INPUTS = {
    "net": ("price.net", "price-net", "net_amount"),
    "tax_rate": ("price.tax_rate", "price-tax-rate", "tax_rate"),
    "commission": ("price.commission", "price-commission", "commission_percentage"),
}

#: computed amount -> test-id path of its read-only output
OUTPUTS = {
    "net": "price-net-normalised",
    "tax": "price-tax-amount",
    "gross": "price-gross",
    "commission": "price-commission-amount",
}


class PriceStep(DemoPage):
    ready = "price.step"

    def open(self, subscription_id: int) -> PriceStep:
        """Deep link straight to the step, skipping the wizard."""
        self.goto(f"/ui/subscriptions/{subscription_id}/step/quote")
        return self

    # -- entering -----------------------------------------------------------------------

    def enter(self, name: str, value: str) -> str:
        """Type an amount into ``name`` and verify it landed (numerically)."""
        key, path, _ = INPUTS[name]
        return fill_amount_and_verify(self.resolve(key), value, description=path)

    def enter_literal(self, name: str, text: str) -> str:
        """Type ``text`` as-is (a sign, a letter) and verify the field holds it verbatim."""
        key, path, _ = INPUTS[name]
        return fill_and_verify(self.resolve(key), text, description=path)

    def clear(self, name: str) -> str:
        field = self.resolve(INPUTS[name][0])
        field.fill("")
        return field.input_value()

    def raw_value(self, name: str) -> str:
        return read_field(self.page, path=INPUTS[name][1]).raw

    def is_read_only(self, name: str) -> bool | None:
        return field_is_read_only(self.page, path=INPUTS[name][1])

    # -- calculating ----------------------------------------------------------------------

    def calculate(self) -> SubmitResult:
        return submit_and_capture(
            self.page,
            lambda: self.resolve("price.calculate").click(),
            QUOTE_URL,
            settle_ms=3000,
        )

    def amount(self, name: str) -> Decimal | None:
        """A computed amount read by its exact test-id path; None when empty."""
        return parse_amount(read_field(self.page, path=OUTPUTS[name]).raw or None)

    def amounts(self) -> dict[str, Decimal | None]:
        return {name: self.amount(name) for name in OUTPUTS}

    def field_error(self, name: str) -> str:
        return self.text("price.field_error", field=INPUTS[name][2])

    def field_errors(self) -> dict[str, str]:
        return {name: self.field_error(name) for name in INPUTS if self.field_error(name)}

    # -- leaving ---------------------------------------------------------------------------

    def activate_immediately(self, on: bool = True) -> None:
        self.resolve("price.activate_immediately").set_checked(on)

    def submit(self) -> SubmitResult:
        """Generate the quote; ``advanced`` = the Confirm step (or the detail page) rendered."""
        return submit_and_capture(
            self.page,
            lambda: self.resolve("price.submit").click(),
            re.compile(r"/api/subscriptions/\d+(/actions/(submit|confirm))?$"),
            method=None,
            advanced=lambda page: "/step/quote" not in page.url,
            settle_ms=4000,
        )


class ConfirmStep(DemoPage):
    ready = "confirm.step"

    def open(self, subscription_id: int) -> ConfirmStep:
        self.goto(f"/ui/subscriptions/{subscription_id}/step/confirm")
        return self

    def status(self) -> str:
        return self.resolve("confirm.status").get_attribute("data-state") or ""

    def monthly_gross(self) -> Decimal | None:
        return parse_amount(self.text("confirm.monthly_gross"))

    def confirm(self) -> SubmitResult:
        return submit_and_capture(
            self.page,
            lambda: self.resolve("confirm.action").click(),
            re.compile(r"/actions/confirm$"),
            advanced=lambda page: "/step/" not in page.url,
            settle_ms=4000,
        )
