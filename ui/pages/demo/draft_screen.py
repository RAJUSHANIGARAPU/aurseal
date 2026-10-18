"""
The draft screen (``/ui/drafts/{id}``): change, end-subscription, renew and note steps.

A draft can be opened from the detail page's action, or created over the API and
deep-linked here, which skips the UI creation entirely. Confirming is captured as
a tracked submit, so a caller can tell a refusal (safe to retry) from an answer
that may have acted (never resend).
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from ui.components import dropdown
from ui.components.date_entry import set_native_date
from ui.components.field_reader import field_is_read_only, read_field
from ui.components.resend_rule import TrackedSubmit, submit_tracking_sent
from ui.components.value_entry import fill_amount_and_verify, fill_and_verify, parse_amount
from ui.components.wizard import has_visible_alerts
from ui.pages.demo.base import DemoPage

CONFIRM_URL = re.compile(r"/api/drafts/\d+/actions/confirm$")


class DraftScreen(DemoPage):
    ready = "draft.kind"

    def open(self, draft_id: int) -> DraftScreen:
        self.goto(f"/ui/drafts/{draft_id}")
        return self

    def kind(self) -> str:
        return self.text("draft.kind")

    def status(self) -> str:
        return self.text("draft.status")

    def steps(self) -> list[str]:
        return [t.strip() for t in self.resolve("draft.step").all_inner_texts()]

    def set_effective_date(self, value: date) -> str:
        return set_native_date(self.resolve("draft.effective_date"), value)

    def clear_effective_date(self) -> str:
        field = self.resolve("draft.effective_date")
        field.fill("")
        return field.input_value()

    def field_error(self, name: str) -> str:
        return self.text("draft.field_error", field=name)

    def alerts_visible(self) -> bool:
        return has_visible_alerts(self.page)

    # -- confirming -------------------------------------------------------------------------

    def confirm(self, action: str, timeout_ms: int = 5000) -> TrackedSubmit:
        """Click the draft's confirm action; ``advanced`` = back on the detail page."""
        return submit_tracking_sent(
            self.page,
            lambda: self.resolve("draft.action", action=action).click(),
            CONFIRM_URL,
            advanced=lambda page: "/ui/subscriptions/" in page.url,
            timeout_ms=timeout_ms,
            settle_ms=3000,
        )

    def discard(self) -> None:
        with self.page.expect_navigation():
            self.resolve("draft.action", action="discard_draft").click()

    # -- end subscription --------------------------------------------------------------------------

    def reasons_offered(self) -> list[str]:
        values = self.resolve("draft.reason").evaluate(
            "s => Array.from(s.options).map(o => o.value).filter(Boolean)"
        )
        return list(values)

    def choose_reason(self, code: str) -> str:
        return dropdown.select_native(self.resolve("draft.reason"), code, by="value")

    def chosen_reason(self) -> str:
        return self.resolve("draft.reason").input_value()

    def tick_precondition(self) -> bool:
        box = self.resolve("draft.closure_precondition")
        box.check()
        return box.is_checked()

    def precondition_error(self) -> str:
        return self.text("draft.precondition_error")

    # -- change ----------------------------------------------------------------------------------

    def set_monthly_net(self, amount: str) -> str:
        return fill_amount_and_verify(self.resolve("draft.monthly_net"), amount)

    def set_addon(self, addon: str, on: bool) -> None:
        self.resolve("draft.addon", addon=addon).set_checked(on)

    def choose_data_region(self, region: str) -> str:
        return dropdown.select_native(self.resolve("draft.data_region"), region, by="value")

    def choose_workspace_type(self, value: str) -> str:
        return dropdown.select_native(self.resolve("draft.workspace_type"), value, by="value")

    def set_spend_cap(self, amount: str) -> str:
        return fill_amount_and_verify(self.resolve("draft.spend_cap"), amount)

    def set_po_reference(self, value: str) -> str:
        return fill_and_verify(self.resolve("draft.po_reference"), value)

    def set_billing_contact(self, name: str, email: str) -> dict[str, str]:
        fill_and_verify(self.resolve("draft.contact_name"), name)
        fill_and_verify(self.resolve("draft.contact_email"), email)
        return {
            "name": self.resolve("draft.contact_name").input_value(),
            "email": self.resolve("draft.contact_email").input_value(),
        }

    def add_invoice_note(self, text: str) -> str:
        return fill_and_verify(self.resolve("draft.add_note"), text)

    def add_label(self, code: str) -> str:
        return dropdown.select_native(self.resolve("draft.add_label"), code, by="value")

    def frequency_read_only(self) -> bool | None:
        return field_is_read_only(self.page, path="draft-frequency")

    # -- renew -----------------------------------------------------------------------------------

    def wait_for_renewal_preview(self) -> None:
        """The preview is filled by the page script, also while its step is hidden."""
        self.resolve("renew.new_price").filter(has_text=re.compile(r"\d")).wait_for(
            state="attached"
        )

    def renewal_values(self) -> dict[str, object]:
        self.wait_for_renewal_preview()
        return {
            "old_term_end": read_field(self.page, path="renewal-old-term-end").raw,
            "new_term_end": read_field(self.page, path="renewal-new-term-end").raw,
            "current_price": parse_amount(self.text("renew.current_price")),
            "new_price": parse_amount(self.text("renew.new_price")),
            "index": read_field(self.page, path="renewal-index").raw,
        }

    def renewal_dates_read_only(self) -> dict[str, bool | None]:
        return {
            name: field_is_read_only(self.page, path=f"renewal-{name}-term-end")
            for name in ("old", "new")
        }

    def enter_index(self, percent: str) -> Decimal | None:
        """Type the index percentage; returns the new price once the preview recomputed."""
        before = self.text("renew.new_price")
        with self.page.expect_response(re.compile(r"/api/drafts/\d+$")):
            fill_amount_and_verify(self.resolve("renew.index"), percent)
        try:
            self.page.wait_for_function(
                "([el, old]) => el.textContent.trim() !== old",
                arg=[self.resolve("renew.new_price").element_handle(), before],
                timeout=3000,
            )
        except Exception:  # noqa: BLE001 - an unchanged price is a value, read it as it is
            pass
        return parse_amount(self.text("renew.new_price"))

    def renewal_next(self) -> None:
        self.resolve("renew.next").click()
        self.resolve("renew.price_step").wait_for()

    def renewal_back(self) -> None:
        self.resolve("renew.back").click()
        self.resolve("renew.indexation_step").wait_for()

    def renewal_step(self) -> str:
        if self.resolve("renew.price_step").is_visible():
            return "price"
        if self.resolve("renew.indexation_step").is_visible():
            return "indexation"
        return ""

    def new_term_end_error(self) -> str:
        return self.text("renew.new_term_end_error")

    # -- note ------------------------------------------------------------------------------------

    def write_note(self, title: str, description: str) -> None:
        fill_and_verify(self.resolve("note.title"), title)
        fill_and_verify(self.resolve("note.description"), description)
