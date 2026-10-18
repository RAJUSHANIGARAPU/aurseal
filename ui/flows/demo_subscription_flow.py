"""
The demo's subscription wizard end to end: parties, price, confirm.

Prerequisites (partner, customer) are expected to exist already, typically
provisioned over the API; the flow selects them by their reference numbers.
Every step's submit outcome is kept, so a test can assert on any of them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from ui.components.submit_outcome import SubmitResult
from ui.pages.demo.price_step import ConfirmStep, PriceStep
from ui.pages.demo.subscription_wizard import SubscriptionWizard


@dataclass
class WizardRun:
    subscription_id: int | None = None
    screening_status: str | None = None
    outcomes: dict[str, SubmitResult] = field(default_factory=dict)
    amounts: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class WizardInput:
    partner_number: str
    customer_number: str
    plan_label: str
    start: date
    frequency: str = "Monthly"
    workspace_type: str | None = None
    data_region: str | None = None
    net: str | None = None
    tax_rate: str | None = None
    commission: str | None = None
    activate_immediately: bool = False


class SubscriptionWizardFlow:
    def __init__(self, page, base_url: str) -> None:
        self.wizard = SubscriptionWizard(page, base_url)
        self.price = PriceStep(page, base_url)
        self.confirm_step = ConfirmStep(page, base_url)

    def prepare_parties(self, data: WizardInput, run: WizardRun) -> None:
        """Fill the Parties step without submitting it."""
        self.wizard.open()
        self.wizard.pick_partner(data.partner_number)
        self.wizard.pick_customer(data.customer_number)
        if self.wizard.screening_required():
            run.screening_status = self.wizard.run_screening()
        self.wizard.choose_plan(data.plan_label)
        self.wizard.choose_frequency(data.frequency)
        self.wizard.set_start_date(data.start)
        if data.data_region:
            self.wizard.choose_data_region(data.data_region)
        if data.workspace_type:
            self.wizard.choose_workspace_type(data.workspace_type)

    def parties(self, data: WizardInput, run: WizardRun) -> None:
        self.prepare_parties(data, run)
        run.outcomes["parties"] = outcome = self.wizard.submit_parties()
        if not outcome.advanced:
            raise AssertionError(f"Parties step did not advance: {outcome.describe()}")
        run.subscription_id = self.wizard.created_id()

    def quote(self, data: WizardInput, run: WizardRun) -> None:
        self.price.wait_ready()
        for name, value in (
            ("net", data.net),
            ("tax_rate", data.tax_rate),
            ("commission", data.commission),
        ):
            if value is not None:
                self.price.enter(name, value)
        run.outcomes["calculate"] = self.price.calculate()
        run.amounts = self.price.amounts()
        if data.activate_immediately:
            self.price.activate_immediately()
        run.outcomes["quote"] = outcome = self.price.submit()
        if not outcome.advanced:
            raise AssertionError(f"Price step did not advance: {outcome.describe()}")

    def confirm(self, run: WizardRun) -> None:
        self.confirm_step.wait_ready()
        run.outcomes["confirm"] = outcome = self.confirm_step.confirm()
        if not outcome.advanced:
            raise AssertionError(f"Confirm step did not advance: {outcome.describe()}")

    def create(self, data: WizardInput) -> WizardRun:
        """Parties -> Price -> Confirm (Confirm is skipped when activated immediately)."""
        run = WizardRun()
        self.parties(data, run)
        self.quote(data, run)
        if not data.activate_immediately:
            self.confirm(run)
        return run
