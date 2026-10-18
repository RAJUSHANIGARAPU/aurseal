"""Status, form-error and document assertions on the detail and price screens."""

from __future__ import annotations

import re
from datetime import timedelta

import pytest

from demo_sut.billing import PRICE_MESSAGES
from oracles import pdf_text
from tests.demo_examples.ui import support
from ui.components.wizard import has_visible_alerts
from ui.pages.demo.price_step import PriceStep
from ui.pages.demo.subscription_detail import SubscriptionDetail

pytestmark = pytest.mark.demo_ui


def _date_line(label: str, value) -> re.Pattern[str]:
    return re.compile(rf"{label}:\s*{value.isoformat()}(?!\d)")


def test_status_errors_and_documents(demo_page, demo_sut, demo_control, demo_seed):
    demo_control.freeze(support.NOW)
    sub = support.booked(demo_seed, "company")
    detail = SubscriptionDetail(demo_page, demo_sut.base_url).open(sub["id"])

    # status and a clean screen
    assert detail.status() == "active" and detail.number() == sub["number"]
    assert not has_visible_alerts(demo_page), "an alert is visible on a healthy detail page"
    assert detail.error_banner() == ""

    # documents: listed, downloadable, and the dates inside are the record's dates
    assert detail.document_rows() >= 1
    assert "account_opened" in detail.document_templates()
    letter = detail.download_document_for("account_opened")
    assert letter.size > 0 and letter.suggested_name.endswith(".pdf"), letter.suggested_name
    if not pdf_text.available():
        pytest.skip("pypdf is not installed; the document body cannot be read")
    text = pdf_text.extract_text(letter.data)
    assert sub["number"] in text
    start, end = support.date_of(sub["start_date"]), support.date_of(sub["term_end"])
    for label, value in (("Starts on", start), ("Term ends", end)):
        assert _date_line(label, value).search(text), f"{label} {value} not in the document"
        for shifted in (value - timedelta(days=1), value + timedelta(days=1)):
            assert not _date_line(label, shifted).search(text), (
                f"{label} is shifted by a day: {shifted} instead of {value}"
            )

    # a form error is named on the field it belongs to
    draft_sub = support.segment_subscription(demo_seed, "company", state="draft")
    price = PriceStep(demo_page, demo_sut.base_url).open(draft_sub["id"])
    price.enter("tax_rate", "150")
    outcome = price.calculate()
    assert outcome.status == 422 and outcome.rejected
    assert price.field_errors() == {"tax_rate": PRICE_MESSAGES["tax_rate"]}
    assert has_visible_alerts(demo_page)


def test_individual_subscription_assertions(demo_page, demo_sut, demo_control, demo_seed):
    """Created, sections shown, no errors, status; then ended, with its transaction."""
    demo_control.freeze(support.NOW)
    sub = support.booked(demo_seed, "individual")
    detail = SubscriptionDetail(demo_page, demo_sut.base_url).open(sub["id"])
    assert detail.subscription_id() == sub["id"] and detail.number().startswith("SUB-")
    assert detail.current_version() == 1 and detail.version_is_current()
    for section in ("Ledger", "Documents", "Messages", "Notes"):
        assert detail.section_visible(section), f"{section} section not shown"
    assert not has_visible_alerts(demo_page) and detail.status_contains("activ")
    assert not detail.is_cancelled()
    assert detail.field("plan-kind") == "INDIVIDUAL"

    draft = demo_seed.draft(sub["id"], "cancel", reason="OTHER")
    demo_seed.confirm_draft(draft["id"])
    detail.open(sub["id"])
    assert detail.is_cancelled()
    kinds = detail.ledger_kinds()
    assert kinds.count("CANCEL") == 1 and kinds[0] == "NEW", kinds
    (cancel,) = [row for row in detail.ledger() if row["kind"] == "CANCEL"]
    assert cancel["period_start"] == "2026-03-10" and cancel["net"] < 0, cancel
