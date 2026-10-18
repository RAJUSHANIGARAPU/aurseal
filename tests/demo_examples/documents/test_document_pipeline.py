"""Document pipeline round trip on the demo: issue, store, read back, render directly.

Uses the project's :class:`api.clients.document_service.DocumentServiceClient`, so the
examples exercise the same client a suite would point at a real render service.
"""

from __future__ import annotations

import hashlib
import uuid

from demo_sut.client import DemoClient
from demo_sut.seeding import DemoSeeder
from oracles.pdf_text import extract_text
from oracles.pdf_text_view import is_pdf
from tests.demo_examples.documents._support import FROZEN_NOW, START_DATE


def test_document_service_client_against_demo(documents):
    ids = documents.template_ids()
    assert {"invoice", "order_summary", "account_opened"} <= set(ids)
    source = documents.template_source("invoice").decode()
    assert "{{invoice.total|money}}" in source
    pdf = documents.render("invoice", {"invoice": {"number": "INV-T7", "total": 9.4}})
    text = extract_text(pdf)
    assert "Invoice INV-T7" in text and "Amount due: EUR 9.40" in text


def test_render_and_storage_round_trip(demo_sut, demo_control, documents):
    run_tag = f"docs-roundtrip-{uuid.uuid4().hex[:10]}"
    seeder = DemoSeeder(DemoClient(demo_sut.base_url, run_tag=run_tag))
    demo_control.freeze(FROZEN_NOW)
    sub = seeder.subscription(start_date=START_DATE)

    issued = documents.documents_of(sub["id"])
    assert issued, f"no documents issued for {sub['number']}"
    # created during this run, and tagged with a template the service lists
    assert {d["run_tag"] for d in issued} == {run_tag}
    deployed = set(documents.template_ids())
    assert {d["template"] for d in issued} >= {"order_summary", "account_opened"}
    assert {d["template"] for d in issued} <= deployed

    files = {f["id"]: f for f in demo_sut.client.ok("GET", "/api/files")}
    for doc in issued:
        pdf = documents.document_pdf(doc["id"])
        stored = documents.file(doc["file_id"])
        assert pdf == stored, f"document {doc['id']} differs from its stored file"
        meta = files[doc["file_id"]]
        assert meta["size"] == len(stored) == doc["size"]
        assert meta["sha256"] == hashlib.sha256(stored).hexdigest()
        assert sub["number"] in extract_text(pdf)

    direct = documents.render(
        "account_opened", {"customer": {"name": "Pell Roundtrip"}, "subscription": sub}
    )
    assert is_pdf(direct) and "Account opened for Pell Roundtrip" in extract_text(direct)
