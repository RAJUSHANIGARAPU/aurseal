"""Customer rules as the screens show them: field errors on the form, and the e-mail
delivery rule on the Parties step."""

from __future__ import annotations

import pytest

from demo_sut.customers import MESSAGES
from oracles.rejection import Signal
from tests.demo_examples.ui import support
from ui.pages.demo.customer_form import CustomerMaintenance
from ui.pages.demo.subscription_wizard import SubscriptionWizard
from verification.outcome_classifier import StepState, step_outcome

ADDRESS = {"line1": "3 Tern Quay", "postal_code": "TQ 51", "city": "Saltmere", "country": "XA"}


@pytest.mark.demo_ui
def test_a_bad_postal_code_is_shown_on_its_field(demo_page, demo_sut, demo_control):
    form = CustomerMaintenance(demo_page, demo_sut.base_url).open_new()
    form.fill_identity(display_name="Form Reader")
    form.set_address(**{**ADDRESS, "postal_code": "51/7"})
    form.set_email("form.reader@example.test")

    refused = form.save()
    assert refused.status == 422 and not refused.advanced
    assert refused.rejected and Signal.API_STATUS in refused.signals().fired
    assert form.field_error("billing_address-postal_code") == MESSAGES["postal_code_invalid"]
    assert refused.body_json()["fields"] == {
        "billing_address.postal_code": MESSAGES["postal_code_invalid"]
    }

    form.set_address(postal_code="TQ 51")
    accepted = form.save()
    assert accepted.status == 201 and accepted.advanced, accepted.describe()
    stored = demo_sut.client.ok("GET", f"/api/customers/{form.saved_id()}")
    assert stored["billing_address"]["postal_code"] == "TQ 51"


#: (customer has an e-mail, delivery chosen) -> what the Parties step must do
DELIVERY_MATRIX = {
    (True, "EMAIL"): StepState.FILLED_AND_ADVANCED,
    (False, "PORTAL"): StepState.FILLED_AND_ADVANCED,
    (False, "EMAIL"): StepState.FILLED_BUT_BLOCKED,
}


@pytest.mark.demo_ui
@pytest.mark.parametrize(("has_email", "delivery"), sorted(DELIVERY_MATRIX))
def test_the_delivery_rule_on_the_parties_step(
    demo_page, demo_sut, demo_control, demo_seed, has_email, delivery
):
    demo_control.freeze(support.NOW)
    partner = demo_seed.partner()
    fields = {} if has_email else {"email": None}
    customer = support.segment_customer(demo_seed, "company", **fields)
    wizard = SubscriptionWizard(demo_page, demo_sut.base_url).open()
    wizard.pick_partner(partner["number"])
    wizard.pick_customer(customer["number"])
    assert wizard.address_block().filled, "control: the billing address was derived"
    wizard.choose_invoice_delivery(delivery)

    message = MESSAGES["email_for_delivery"]
    verdict = step_outcome(wizard.observe_address_block(delivery, message), message)
    assert verdict.state is DELIVERY_MATRIX[(has_email, delivery)], str(verdict)
