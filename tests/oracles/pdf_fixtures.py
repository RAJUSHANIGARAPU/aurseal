"""A minimal, deterministic PDF writer for offline oracle tests (stdlib only).

It writes just enough PDF to carry positioned text in the standard Helvetica
font: a catalog, a page tree, one content stream per page and an Info
dictionary with a producer. Output is byte-for-byte reproducible, so a
committed fixture can be checked against the writer that made it.

Regenerate the committed extraction-contract fixture with::

    python -m tests.oracles.pdf_fixtures
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

FIXTURE_DIR = Path(__file__).parent / "fixtures"
INVOICE_FIXTURE = FIXTURE_DIR / "rendered-invoice.pdf"


@dataclass(frozen=True)
class TextRun:
    x: float
    y: float
    text: str
    size: float = 11
    angle: float = 0.0
    """Rotation in degrees, counter-clockwise; 45 draws a diagonal overlay."""


def _escape(text: str) -> bytes:
    raw = text.encode("cp1252")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def _content(runs: Sequence[TextRun]) -> bytes:
    out = [b"BT"]
    for r in runs:
        cos, sin = math.cos(math.radians(r.angle)), math.sin(math.radians(r.angle))
        out.append(f"/F1 {r.size:g} Tf".encode())
        out.append(f"{cos:.4f} {sin:.4f} {-sin:.4f} {cos:.4f} {r.x:g} {r.y:g} Tm".encode())
        out.append(b"(" + _escape(r.text) + b") Tj")
    out.append(b"ET")
    return b"\n".join(out)


def build_pdf(
    pages: Sequence[Sequence[TextRun]],
    *,
    producer: str = "Example Renderer 1.0",
    width: int = 595,
    height: int = 842,
) -> bytes:
    """A PDF with one page per entry of ``pages``."""
    objects: list[bytes] = []
    n_pages = len(pages)
    font_id = 3 + 2 * n_pages
    info_id = font_id + 1
    page_ids = [3 + 2 * i for i in range(n_pages)]
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{p} 0 R" for p in page_ids)
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode())
    for i, runs in enumerate(pages):
        content_id = page_ids[i] + 1
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] "
                f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>"
            ).encode()
        )
        stream = _content(runs)
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
    objects.append(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
    )
    objects.append(b"<< /Producer (" + _escape(producer) + b") >>")
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info {info_id} 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n"
    ).encode()
    return bytes(out)


def invoice_runs() -> list[list[TextRun]]:
    """The invented two-page invoice behind ``fixtures/rendered-invoice.pdf``."""
    left, value = 60, 220
    page1 = [
        TextRun(left, 790, "INVOICE", size=16),
        TextRun(left, 750, "Invoice number"),
        TextRun(value, 750, "INV-2031-0042"),
        TextRun(left, 732, "Invoice date"),
        TextRun(value, 732, "4 May 2031"),
        TextRun(left, 714, "Customer"),
        TextRun(value, 714, "Example Trading Ltd"),
        TextRun(left, 680, "Delivery address", size=12),
        TextRun(left, 662, "12 Sample Street"),
        TextRun(left, 644, "Exampletown EX1 2AB"),
        TextRun(left, 610, "Item"),
        TextRun(value, 610, "Quantity"),
        TextRun(340, 610, "Amount"),
        TextRun(left, 592, "Desk lamp"),
        TextRun(value, 592, "2"),
        TextRun(340, 592, "1,250.00"),
        TextRun(left, 574, "Cable set"),
        TextRun(value, 574, "1"),
        TextRun(340, 574, "48.50"),
        TextRun(left, 540, "Total due"),
        TextRun(value, 540, "GBP 1,298.50"),
    ]
    page2 = [
        TextRun(left, 790, "Payment terms", size=12),
        TextRun(left, 772, "Due within 30 days of the invoice date."),
        TextRun(left, 754, "Reference"),
        TextRun(value, 754, "ORD-42"),
    ]
    return [page1, page2]


def invoice_pdf() -> bytes:
    return build_pdf(invoice_runs(), producer="Example Renderer 1.0")


if __name__ == "__main__":  # pragma: no cover - regeneration entry point
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    INVOICE_FIXTURE.write_bytes(invoice_pdf())
    print(f"wrote {INVOICE_FIXTURE} ({INVOICE_FIXTURE.stat().st_size} bytes)")
