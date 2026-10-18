"""Seeding and reading helpers shared by the document examples.

Seed values come from ``fixtures/seed-content.json`` (invented), so the offline oracle
self-tests and the live examples agree on what a document has to print.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from api.clients.document_service import DocumentServiceClient
from oracles.pdf_text import extract_text

HERE = Path(__file__).parent
FIXTURES = HERE / "fixtures"
REPORTS_DIR = Path("reports") / "demo_examples" / "documents"

FROZEN_NOW = "2026-06-03T09:00:00Z"
START_DATE = "2026-06-01"
BOOK_UNTIL = "2026-07-15"
CHANGE_EFFECTIVE = "2026-06-11"
CANCEL_EFFECTIVE = "2026-07-16"

#: Documents that carry a ``## Details`` section.
DETAIL_TEMPLATES = ("order_summary", "account_opened", "plan_update_notice")
#: The document issued on activation and the one issued by a change, with their headings.
CONTENT_TEMPLATES = {"new": "account_opened", "change": "plan_update_notice"}
HEADINGS = {"account_opened": "Account opened for", "plan_update_notice": "Plan update for"}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def seed_content() -> dict[str, Any]:
    return load_json(FIXTURES / "seed-content.json")


def plus_days(day: str, days: int) -> str:
    return (date.fromisoformat(day) + timedelta(days=days)).isoformat()


def content_fields(*, with_contact: bool = True) -> dict[str, Any]:
    """Subscription fields the document examples seed (contact optional, the rest fixed)."""
    content = seed_content()
    fields = {k: content[k] for k in ("workspace_type", "data_region")}
    if with_contact:
        fields["billing_contact"] = content["billing_contact"]
    return fields


def seed_with_documents(
    demo_control: Any,
    demo_seed: Any,
    *,
    plan_kind: str = "TEAM",
    change: bool = True,
    cancel: bool = False,
    **fields: Any,
) -> dict[str, Any]:
    """An active subscription with its order summary and account-opened letter, optionally
    changed and ended. The clock is frozen first so every date is deterministic. Returns the
    latest subscription view."""
    content = seed_content()
    demo_control.freeze(FROZEN_NOW)
    sub = demo_seed.subscription(start_date=START_DATE, plan_kind=plan_kind, **fields)
    demo_seed.book_due(sub["id"], until=BOOK_UNTIL)
    if change:
        draft = demo_seed.draft(
            sub["id"], "change", effective_date=CHANGE_EFFECTIVE, changes=content["change"]
        )
        sub = demo_seed.confirm_draft(draft["id"])
    if cancel:
        draft = demo_seed.draft(
            sub["id"], "cancel", effective_date=CANCEL_EFFECTIVE, reason="NO_LONGER_NEEDED"
        )
        sub = demo_seed.confirm_draft(draft["id"])
    return sub


def document_texts(
    documents: DocumentServiceClient, subscription_id: int, templates: tuple[str, ...]
) -> dict[str, str]:
    """Text of the latest issued document per template (templates not issued are absent)."""
    latest: dict[str, dict[str, Any]] = {}
    for doc in documents.documents_of(subscription_id):
        if doc["template"] in templates:
            latest[doc["template"]] = doc
    return {t: extract_text(documents.document_pdf(d["id"])) for t, d in latest.items()}
