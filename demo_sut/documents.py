"""Document templates, a template renderer and a hand-written minimal PDF writer.

Template syntax (one output line per template line):

* ``{{path.to.value}}`` - replaced by the value at that dotted path in the render payload;
  ``{{path|money}}`` formats it as ``EUR 12.34``. An unresolved path stays in the output
  literally, so a merge-expression check can find it.
* a line starting with ``?{path}`` is rendered only when ``path`` has a non-empty value
  (a conditional line vanishes completely, label included).
* a line starting with ``## `` starts a named section (``## Details``).

The PDF writer emits PDF 1.4 with the standard Helvetica font and no external dependency;
``pypdf`` (or any reader) extracts its text.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from demo_sut.core import Context, NotFound, fmt_money

TEMPLATES: dict[str, dict[str, str]] = {
    "order_summary": {
        "title": "Order summary",
        "source": "\n".join(
            [
                "# Order summary for {{subscription.number}}",
                "Monthly charge: {{price.monthly_gross|money}}",
                "Customer: {{customer.name}}",
                "Reseller: {{partner.name}}",
                "## Details",
                "Data region: {{subscription.data_region}}",
                "?{subscription.workspace_type}Workspace: {{subscription.workspace_type}}",
                "Net monthly: {{price.monthly_net|money}}",
                "Tax monthly: {{price.monthly_tax|money}}",
                "?{fees.net}Setup fee: {{fees.net|money}}",
                "?{subscription.po_reference}PO reference: {{subscription.po_reference}}",
            ]
        ),
    },
    "account_opened": {
        "title": "Account opened",
        "source": "\n".join(
            [
                "# Account opened for {{customer.name}}",
                "Subscription: {{subscription.number}}",
                "Starts on: {{subscription.start_date}}",
                "Term ends: {{subscription.term_end}}",
                "Charge per period: {{price.period_gross|money}}",
                "## Details",
                "Data region: {{subscription.data_region}}",
                "?{subscription.workspace_type}Workspace: {{subscription.workspace_type}}",
                "?{billing_contact.name}Billing contact: {{billing_contact.name}}",
                "?{subscription.po_reference}PO reference: {{subscription.po_reference}}",
            ]
        ),
    },
    "plan_update_notice": {
        "title": "Plan update notice",
        "source": "\n".join(
            [
                "# Plan update for {{subscription.number}}",
                "Version: {{subscription.version}}",
                "Adjustment: {{change.total|money}}",
                "Applies from: {{change.period_start}}",
                "New charge per period: {{price.period_gross|money}}",
                "Customer: {{customer.name}}",
                "## Details",
                "Data region: {{subscription.data_region}}",
                "?{subscription.workspace_type}Workspace: {{subscription.workspace_type}}",
                "?{subscription.po_reference}PO reference: {{subscription.po_reference}}",
            ]
        ),
    },
    "closure_notice": {
        "title": "Closure notice",
        "source": "\n".join(
            [
                "# Closure of {{subscription.number}}",
                "Credit: {{cancel.total|money}}",
                "Credit net: {{cancel.net|money}}",
                "Credit tax: {{cancel.tax|money}}",
                "Ends on: {{cancel.period_start}}",
                "Reason: {{closure.reason}}",
                "Customer: {{customer.name}}",
                "?{subscription.po_reference}PO reference: {{subscription.po_reference}}",
            ]
        ),
    },
    "term_extension_notice": {
        "title": "Term extension notice",
        "source": "\n".join(
            [
                "# Term extension for {{subscription.number}}",
                "New charge per period: {{price.period_gross|money}}",
                "New term: {{subscription.term_start}} - {{subscription.term_end}}",
                "Customer: {{customer.name}}",
            ]
        ),
    },
    "invoice": {
        "title": "Invoice",
        "source": "\n".join(
            [
                "# Invoice {{invoice.number}}",
                "Amount due: {{invoice.total|money}}",
                "Net: {{invoice.net|money}}",
                "Tax: {{invoice.tax|money}}",
                "Billing period: {{invoice.period_start}} - {{invoice.period_end}}",
                "Subscription: {{subscription.number}}",
                "Customer: {{customer.name}}",
                "?{invoice.due_date}Pay by: {{invoice.due_date}}",
                "?{subscription.po_reference}PO reference: {{subscription.po_reference}}",
            ]
        ),
    },
}

PLACEHOLDER = re.compile(r"\{\{\s*([\w.]+)(\|money)?\s*\}\}")


def _lookup(payload: dict[str, Any], path: str) -> Any:
    node: Any = payload
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def render_lines(source: str, payload: dict[str, Any]) -> list[str]:
    """Apply the template syntax; returns output lines (section headers kept as ``## x``)."""
    out: list[str] = []
    for raw in source.splitlines():
        line = raw
        if line.startswith("?{"):
            path, line = line[2:].split("}", 1)
            if _lookup(payload, path) in (None, "", [], 0, 0.0):
                continue

        def sub(match: re.Match[str]) -> str:
            value = _lookup(payload, match.group(1))
            if value is None:
                return match.group(0)
            return fmt_money(value) if match.group(2) else str(value)

        out.append(PLACEHOLDER.sub(sub, line))
    return out


# -- PDF -----------------------------------------------------------------------


def _pdf_text(text: str) -> str:
    safe = text.encode("latin-1", "replace").decode("latin-1")
    return safe.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def write_pdf(lines: list[str], *, title: str = "Document", watermark: str | None = None) -> bytes:
    """A minimal valid PDF: one Helvetica text line per entry, 48 lines per A4 page."""
    pages = [lines[i : i + 48] for i in range(0, len(lines), 48)] or [[]]
    objects: list[bytes] = []
    page_ids = [4 + 2 * i for i in range(len(pages))]
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for i, page_lines in enumerate(pages):
        ops = ["BT", "/F1 11 Tf", "14 TL", "56 790 Td"]
        for line in page_lines:
            if line.startswith("# "):
                ops += ["/F1 15 Tf", f"({_pdf_text(line[2:])}) Tj", "T*", "/F1 11 Tf"]
            elif line.startswith("## "):
                ops += ["T*", f"({_pdf_text(line[3:])}) Tj", "T*"]
            else:
                ops += [f"({_pdf_text(line)}) Tj", "T*"]
        ops.append("ET")
        if watermark:
            ops += [
                "BT",
                "/F1 60 Tf",
                "0.85 g",
                "0.7071 0.7071 -0.7071 0.7071 150 300 Tm",
                f"({_pdf_text(watermark)}) Tj",
                "ET",
            ]
        stream = "\n".join(ops).encode("latin-1")
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {page_ids[i] + 1} 0 R >>".encode()
        )
        objects.append(
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
        )
    info_id = len(objects) + 1
    objects.append(f"<< /Title ({_pdf_text(title)}) /Producer (demo_sut) >>".encode())
    body = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(body))
        body += f"{number} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_at = len(body)
    body += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        body += f"{offset:010d} 00000 n \n".encode()
    body += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info {info_id} 0 R >>\n"
        f"startxref\n{xref_at}\n%%EOF\n"
    ).encode()
    return bytes(body)


# -- service --------------------------------------------------------------------


class DocumentRenderer:
    """Renders templates to PDF and stores documents and their files."""

    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx

    def templates(self) -> list[dict[str, str]]:
        return [{"id": key, "title": t["title"]} for key, t in sorted(TEMPLATES.items())]

    def source(self, template: str) -> str:
        if template not in TEMPLATES:
            raise NotFound("template", template)
        return TEMPLATES[template]["source"]

    def render(self, template: str, payload: dict[str, Any], watermark: str | None = None):
        source = self.source(template)
        if template == "account_opened":
            if self.ctx.fault("doc_amount_unformatted"):
                source = source.replace("{{price.period_gross|money}}", "{{price.period_gross}}")
            if self.ctx.fault("doc_optional_prints_none"):
                contact = payload.get("billing_contact") or {}
                if not contact.get("name"):
                    payload = {**payload, "billing_contact": {**contact, "name": str(None)}}
        lines = render_lines(source, payload)
        return write_pdf(lines, title=TEMPLATES[template]["title"], watermark=watermark)

    def payload_for(self, sub: dict[str, Any], extra: dict[str, Any] | None = None) -> dict:
        customer = self.ctx.store.get("customers", sub.get("customer_id")) or {}
        return {
            "subscription": {
                k: sub.get(k)
                for k in (
                    "number",
                    "version",
                    "start_date",
                    "term_start",
                    "term_end",
                    "data_region",
                    "workspace_type",
                    "po_reference",
                )
            },
            "customer": {"name": customer.get("display_name"), "number": customer.get("number")},
            "partner": sub.get("partner") or {},
            "price": sub.get("price") or {},
            "fees": sub.get("fees") or {},
            "closure": sub.get("closure") or {},
            "billing_contact": sub.get("billing_contact") or {},
            "date": self.ctx.clock.today().isoformat(),
            **(extra or {}),
        }

    def store_file(self, name: str, content_type: str, body: bytes) -> dict[str, Any]:
        blob = self.ctx.store.put_blob(body)
        return self.ctx.store.insert(
            "files",
            {
                "name": name,
                "content_type": content_type,
                "size": len(body),
                "sha256": hashlib.sha256(body).hexdigest(),
                "blob_id": blob,
                "created_at": self.ctx.stamp(),
            },
        )

    def issue(
        self, sub: dict[str, Any], template: str, extra: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Render a template for a subscription and store it as a document + file."""
        payload = self.payload_for(sub, extra)
        body = self.render(template, payload)
        file = self.store_file(
            f"{sub['number']}-{template}-v{sub['version']}.pdf", "application/pdf", body
        )
        return self.ctx.store.insert(
            "documents",
            {
                "subscription_id": sub["id"],
                "version": sub["version"],
                "template": template,
                "file_id": file["id"],
                "size": file["size"],
                "file_name": file["name"],
                "created_at": self.ctx.stamp(),
                "run_tag": self.ctx.run_tag,
            },
        )

    def issue_invoice(self, tx: dict[str, Any], invoice: dict[str, Any]) -> None:
        sub = self.ctx.store.get("subscriptions", tx["subscription_id"]) or {}
        doc = self.issue(sub, "invoice", {"invoice": invoice})
        invoice["document_id"] = doc["id"]
        self.ctx.store.save("invoices", invoice)

    def get(self, doc_id: Any) -> dict[str, Any]:
        doc = self.ctx.store.get("documents", doc_id)
        if doc is None:
            raise NotFound("document", doc_id)
        return doc

    def file(self, file_id: Any) -> tuple[dict[str, Any], bytes]:
        meta = self.ctx.store.get("files", file_id)
        if meta is None:
            raise NotFound("file", file_id)
        return meta, self.ctx.store.get_blob(meta["blob_id"]) or b""
