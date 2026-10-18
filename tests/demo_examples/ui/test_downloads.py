"""Message and document downloads from the detail page deliver bytes and a file name."""

from __future__ import annotations

import pytest

from tests.demo_examples.ui import support
from ui.pages.demo.subscription_detail import SubscriptionDetail
from verification.evidence_verdict import EvidenceVerdict

pytestmark = pytest.mark.demo_ui


def test_message_and_document_downloads_have_bytes_and_name(
    demo_page, demo_sut, demo_control, demo_seed
):
    demo_control.freeze(support.NOW)
    sub = support.booked(demo_seed)  # the booking hands a message to the outbox
    demo_sut.client.ok("POST", "/api/messages/generate", {"subscription_id": sub["id"]})
    detail = SubscriptionDetail(demo_page, demo_sut.base_url).open(sub["id"])
    for _ in range(30):  # the generation job runs in the background
        if any(m["status"] == "TO_BE_SENT" for m in detail.message_rows()):
            break
        demo_page.wait_for_timeout(200)
        detail.open(sub["id"])
    verdict = EvidenceVerdict("detail page downloads")
    ready = [m for m in detail.message_rows() if m["status"] == "TO_BE_SENT"]
    assert ready, f"no generated message to download: {detail.message_rows()}"

    message = detail.download_message(0)
    assert message.size > 0 and message.suggested_name, message.suggested_name
    document = detail.download_document(0)
    assert document.size > 0 and document.suggested_name.endswith(".pdf")
    assert document.data.startswith(b"%PDF")
    verdict.proved(
        f"message {message.suggested_name} ({message.size} B), "
        f"document {document.suggested_name} ({document.size} B)"
    )
