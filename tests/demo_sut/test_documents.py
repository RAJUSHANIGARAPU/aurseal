"""Templates, the hand-written PDF writer and stored documents."""

from __future__ import annotations

import io

import pytest
from pypdf import PdfReader

from demo_sut.documents import TEMPLATES, render_lines, write_pdf


def pdf_text(body: bytes) -> str:
    reader = PdfReader(io.BytesIO(body))
    return "\n".join(page.extract_text() for page in reader.pages)


def document_text(demo_sut, sub_id: int, template: str) -> str:
    docs = demo_sut.client.ok("GET", f"/api/subscriptions/{sub_id}/documents")
    doc = [d for d in docs if d["template"] == template][-1]
    return pdf_text(demo_sut.client.get(f"/api/documents/{doc['id']}.pdf").body)


def test_the_writer_produces_a_valid_multi_page_pdf():
    lines = ["# Heading (in brackets) \\ with a backslash"] + [f"Row {i}" for i in range(100)]
    body = write_pdf(lines, title="Sample", watermark=None)
    assert body.startswith(b"%PDF-1.4") and body.rstrip().endswith(b"%%EOF")
    reader = PdfReader(io.BytesIO(body))
    assert len(reader.pages) == 3
    assert reader.metadata.title == "Sample"
    text = pdf_text(body)
    assert "Heading (in brackets) \\ with a backslash" in text and "Row 99" in text


def test_conditional_lines_vanish_and_unknown_slots_stay_visible():
    source = "A: {{a}}\n?{b}B: {{b}}\nC: {{no.such.path}}\nM: {{m|money}}"
    lines = render_lines(source, {"a": "x", "b": "", "m": 7.25})
    assert lines == ["A: x", "C: {{no.such.path}}", "M: EUR 7.25"]


def test_templates_and_their_sources(demo_sut, demo_control):
    ids = {t["id"] for t in demo_sut.client.ok("GET", "/api/templates")}
    assert (
        ids
        == set(TEMPLATES)
        == {
            "order_summary",
            "account_opened",
            "plan_update_notice",
            "closure_notice",
            "term_extension_notice",
            "invoice",
        }
    )
    source = demo_sut.client.get("/api/templates/invoice/source")
    assert source.status == 200 and b"{{invoice.total|money}}" in source.body
    assert demo_sut.client.get("/api/templates/nope/source").status == 404


@pytest.mark.parametrize("watermark", [None, "DRAFT COPY"])
def test_the_render_endpoint_with_and_without_a_watermark(demo_sut, demo_control, watermark):
    payload = {
        "invoice": {
            "number": "300118",
            "total": 18.6,
            "net": 15.5,
            "tax": 3.1,
            "period_start": "2026-02-01",
            "period_end": "2026-03-01",
        },
        "subscription": {"number": "SUB-009001"},
        "customer": {"name": "Unit Reader"},
    }
    response = demo_sut.client.post(
        "/api/render", {"template": "invoice", "payload": payload, "watermark": watermark}
    )
    assert response.status == 200 and response.headers["content-type"] == "application/pdf"
    text = pdf_text(response.body)
    assert "Amount due: EUR 18.60" in text
    assert ("DRAFT COPY" in text) is (watermark is not None)
    assert "Pay by" not in text and "PO reference" not in text


def test_a_stored_document_reads_back_byte_identical(demo_sut, demo_seed):
    demo_seed.client.freeze("2026-03-10T09:00:00Z")
    sub = demo_seed.subscription(start_date="2026-03-01")
    docs = demo_sut.client.ok("GET", f"/api/subscriptions/{sub['id']}/documents")
    opened = next(d for d in docs if d["template"] == "account_opened")
    pdf = demo_sut.client.get(f"/api/documents/{opened['id']}.pdf")
    stored = demo_sut.client.get(f"/api/files/{opened['file_id']}")
    assert pdf.body == stored.body and len(pdf.body) == opened["size"]
    assert 'filename="' in pdf.headers["content-disposition"]
    assert sub["number"] in pdf_text(pdf.body)


def test_conditional_lines_follow_the_subscription(demo_sut, demo_seed, demo_control):
    bare = demo_seed.subscription()
    full = demo_seed.subscription(
        workspace_type="SANDBOX",
        po_reference="PO-UNIT-77",
        billing_contact={"name": "Ivo Billing", "email": "ivo@example.test"},
        data_region="AP-SOUTH",
    )
    bare_text = document_text(demo_sut, bare["id"], "account_opened")
    full_text = document_text(demo_sut, full["id"], "account_opened")
    for label in ("Workspace:", "Billing contact:", "PO reference:"):
        assert label not in bare_text
        assert label in full_text
    assert "Data region: AP-SOUTH" in full_text and "Data region: EU-CENTRAL" in bare_text
    assert "Billing contact: Ivo Billing" in full_text


def test_change_and_closure_documents_carry_their_amounts(demo_sut, demo_seed):
    demo_seed.client.freeze("2026-03-10T09:00:00Z")
    sub = demo_seed.subscription(start_date="2026-03-01")
    demo_seed.book_due(sub["id"])
    change = demo_seed.draft(
        sub["id"], "change", effective_date="2026-03-20", changes={"monthly_net": 44}
    )
    demo_seed.confirm_draft(change["id"])
    change_tx = next(t for t in demo_seed.transactions(sub["id"]) if t["kind"] == "CHANGE")
    closure = demo_seed.draft(sub["id"], "cancel", effective_date="2026-03-25", reason="BUDGET")
    demo_seed.confirm_draft(closure["id"])
    cancel_tx = next(t for t in demo_seed.transactions(sub["id"]) if t["kind"] == "CANCEL")
    notice = document_text(demo_sut, sub["id"], "plan_update_notice")
    assert f"Adjustment: EUR {change_tx['total']:.2f}" in notice
    assert "Version: 2" in notice
    closing = document_text(demo_sut, sub["id"], "closure_notice")
    assert f"Credit: EUR {cancel_tx['total']:.2f}" in closing and "Reason: BUDGET" in closing


def test_an_unknown_document_is_a_service_404(demo_sut):
    assert demo_sut.client.get("/api/documents/999999.pdf").status == 404
    assert demo_sut.client.get("/api/files/999999").status == 404


@pytest.mark.parametrize("fault_on", [False, True])
def test_previous_version_fault(demo_sut, demo_seed, demo_control, fault_on):
    if fault_on:
        demo_control.fault("document_renders_previous_version")
    demo_seed.client.freeze("2026-03-10T09:00:00Z")
    sub = demo_seed.subscription(start_date="2026-03-01", data_region="EU-CENTRAL")
    draft = demo_seed.draft(sub["id"], "change", changes={"data_region": "EU-NORTH"})
    demo_seed.confirm_draft(draft["id"])
    text = document_text(demo_sut, sub["id"], "plan_update_notice")
    assert ("Data region: EU-NORTH" in text) is not fault_on
    assert ("Version: 1" in text) is fault_on


@pytest.mark.parametrize("fault_on", [False, True])
def test_absent_contact_line(demo_sut, demo_seed, demo_control, fault_on):
    if fault_on:
        demo_control.fault("doc_optional_prints_none")
    without = demo_seed.subscription()
    text = document_text(demo_sut, without["id"], "account_opened")
    assert ("Billing contact: None" in text) is fault_on
    named = demo_seed.subscription(billing_contact={"name": "Pia Contact", "email": None})
    assert "Billing contact: Pia Contact" in document_text(demo_sut, named["id"], "account_opened")


@pytest.mark.parametrize("fault_on", [False, True])
def test_period_charge_format(demo_sut, demo_seed, demo_control, fault_on):
    if fault_on:
        demo_control.fault("doc_amount_unformatted")
    sub = demo_seed.subscription()
    text = document_text(demo_sut, sub["id"], "account_opened")
    gross = sub["price"]["period_gross"]
    assert (f"Charge per period: EUR {gross:.2f}" in text) is not fault_on
    assert (f"Charge per period: {gross}" in text) is fault_on
