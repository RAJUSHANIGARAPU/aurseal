"""
The demo's new-subscription wizard: Parties, then Price, then Confirm.

The Parties step picks a partner and a customer in a reference dialog, may ask for a
screening of an unverified customer, shows the customer's billing address and takes plan,
frequency, start date, data region, workspace type and invoice delivery.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from ui.components import dropdown, reference_picker
from ui.components.date_entry import set_native_date
from ui.components.resend_rule import TrackedSubmit, submit_tracking_sent
from ui.components.response_capture import ResponseCapture
from ui.components.submit_outcome import SubmitResult, submit_and_capture
from ui.pages.demo.base import DemoPage
from verification.outcome_classifier import StepObservations

SUBMIT_URL = re.compile(r"/api/subscriptions$")


def exact_number(number: str) -> re.Pattern[str]:
    """``number`` as a whole reference: ``CUS-D1-1`` must not match ``CUS-D1-12``."""
    return re.compile(re.escape(number) + r"(?!\d)")


@dataclass(frozen=True)
class AddressBlock:
    country: str
    text: str

    @property
    def filled(self) -> bool:
        return bool(self.country) and self.text not in ("", "-")


class SubscriptionWizard(DemoPage):
    ready = "wizard.parties_step"

    def open(self) -> SubscriptionWizard:
        self.goto("/ui/subscriptions/new")
        return self

    # -- parties ----------------------------------------------------------------------

    def _pick(self, kind: str, number: str) -> str:
        self.resolve(f"wizard.pick_{kind}").click()
        self.resolve("picker.dialog").wait_for()
        exact = exact_number(number)
        found = reference_picker.pick(self.page, exact, search_term=number)
        self.resolve(f"wizard.{kind}_summary").filter(has_text=exact).wait_for()
        return found

    def pick_partner(self, number: str) -> str:
        """Pick a partner by its reference number; returns where the row was found."""
        return self._pick("partner", number)

    def pick_customer(self, number: str) -> str:
        found = self._pick("customer", number)
        self.wait_for_address_block()
        return found

    def open_customer_picker(self) -> None:
        self.resolve("wizard.pick_customer").click()
        self.resolve("picker.dialog").wait_for()

    def picker_rows(self) -> list[str]:
        return [t.strip() for t in self.resolve("picker.row").all_inner_texts()]

    def enter_customer_number(self, number: str) -> AddressBlock:
        """Direct entry: type the number, look it up, wait for the derived address block."""
        self.resolve("wizard.customer_number").fill(number)
        with ResponseCapture(self.page, re.compile(r"/api/customers/\d+$")) as capture:
            self.resolve("wizard.customer_number_lookup").click()
            summary = self.resolve("wizard.customer_summary")
            summary.filter(has_text=exact_number(number)).wait_for()
            self.wait_for_address_block()
        self.last_customer_capture = capture
        return self.address_block()

    def customer_lookup_status(self) -> str:
        return self.text("wizard.customer_number_status")

    def summary(self, kind: str) -> str:
        return self.text(f"wizard.{kind}_summary")

    def change_offered(self, kind: str) -> bool:
        return self.resolve(f"wizard.{kind}_change").is_visible()

    def wait_for_address_block(self) -> None:
        self.page.wait_for_function(
            "el => el.textContent.trim() !== '-'",
            arg=self.resolve("wizard.address_block").element_handle(),
        )

    def address_block(self) -> AddressBlock:
        block = self.resolve("wizard.address_block")
        return AddressBlock(
            country=block.get_attribute("data-country") or "",
            text=(block.inner_text() or "").strip(),
        )

    def screening_required(self) -> bool:
        return self.resolve("wizard.verification_step").is_visible()

    def run_screening(self) -> str:
        """Run the screening for an unverified customer; returns the status it reports."""
        self.resolve("wizard.screening_run").click()
        status = self.resolve("wizard.screening_status")
        status.filter(has_text=re.compile(r"\w")).wait_for()
        return status.inner_text().strip()

    def choose_plan(self, label: str) -> str:
        return dropdown.select_native(self.resolve("wizard.plan"), label)

    def choose_frequency(self, label: str) -> str:
        return dropdown.select_native(self.resolve("wizard.frequency"), label)

    def choose_data_region(self, value: str) -> str:
        return dropdown.select_native(self.resolve("wizard.data_region"), value, by="value")

    def choose_workspace_type(self, value: str) -> str:
        return dropdown.select_native(self.resolve("wizard.workspace_type"), value, by="value")

    def choose_invoice_delivery(self, value: str) -> str:
        return dropdown.select_native(self.resolve("wizard.invoice_delivery"), value, by="value")

    def set_start_date(self, value: date) -> str:
        return set_native_date(self.resolve("wizard.start_date"), value)

    def step_error(self) -> str:
        return self.text("wizard.step_error")

    def submit_parties(self) -> SubmitResult:
        """Submit the Parties step; ``advanced`` means the Price step rendered."""
        return submit_and_capture(
            self.page,
            lambda: self.resolve("wizard.parties_submit").click(),
            SUBMIT_URL,
            advanced=lambda page: "/step/quote" in page.url,
            settle_ms=4000,
        )

    def submit_parties_tracked(self) -> TrackedSubmit:
        """Submit the Parties step, also recording whether the create call was sent."""
        return submit_tracking_sent(
            self.page,
            lambda: self.resolve("wizard.parties_submit").click(),
            SUBMIT_URL,
            advanced=lambda page: "/step/quote" in page.url,
            settle_ms=4000,
        )

    def created_id(self) -> int:
        """The new subscription's id, read from the Price step's URL."""
        match = re.search(r"/ui/subscriptions/(\d+)/step/quote", self.page.url)
        if not match:
            raise AssertionError(f"not on a Price step: {self.page.url}")
        return int(match.group(1))

    def observe_address_block(self, case: str, rejection_message: str) -> StepObservations:
        """
        Read the address block, submit the step, and record what happened.

        The observations feed ``verification.outcome_classifier.step_outcome``; the
        page object only observes and never judges.
        """
        rendered = self.resolve("wizard.parties_step").is_visible()
        present = self.resolve("wizard.address_block").count()
        block = self.address_block() if present else AddressBlock("", "")
        outcome = self.submit_parties()
        message = self.step_error() if self.resolve("wizard.step_error").count() else ""
        return StepObservations(
            case=case,
            step_rendered=rendered,
            fields_present=present,
            block_value=block.text if block.filled else "",
            submit_clicked=True,
            submit_status=-1 if outcome.status is None else outcome.status,
            advanced=bool(outcome.advanced),
            rejection_message_shown=rejection_message in message,
            validation_surfaces=[*outcome.inline_errors, *outcome.alerts],
        )
