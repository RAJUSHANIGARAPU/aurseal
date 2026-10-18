"""Tests for oracles.pdf_text_view: parse-once inspection of a downloaded PDF."""

from __future__ import annotations

import pytest

pytest.importorskip("pypdf")

from oracles.pdf_text_view import (  # noqa: E402
    DEFAULT_OVERLAY_PHRASES,
    PHRASES_KEY,
    NotAPdf,
    PdfTextView,
    is_pdf,
    overlay_phrases,
    squash,
    watermark_findings,
)
from tests.oracles.pdf_fixtures import TextRun, build_pdf, invoice_pdf  # noqa: E402


def _stamped(text: str, *, per_glyph: bool = False, producer: str = "Example Renderer 1.0"):
    body = [TextRun(60, 750 - 18 * i, f"Body line {i}") for i in range(12)]
    if per_glyph:
        overlay = [
            TextRun(100 + 22 * i, 300 + 22 * i, ch, size=36, angle=45) for i, ch in enumerate(text)
        ]
    else:
        overlay = [TextRun(120, 320, text, size=40, angle=45)]
    return build_pdf([body + overlay], producer=producer)


class TestParse:
    def test_reads_producer_pages_and_text(self):
        doc = PdfTextView.parse(invoice_pdf())
        assert doc.page_count == 2
        assert doc.producer == "Example Renderer 1.0"
        assert "INV-2031-0042" in doc.text and "INV-2031-0042" in doc.plain_text
        assert "2 page(s)" in doc.describe()

    def test_rejects_non_pdf_bytes(self):
        with pytest.raises(NotAPdf, match="starts with '<html>"):
            PdfTextView.parse(b"<html>error</html>")
        assert not is_pdf(b"")
        assert is_pdf(b"\n%PDF-1.7")

    def test_produced_by_is_a_case_insensitive_substring(self):
        doc = PdfTextView.parse(invoice_pdf())
        assert doc.produced_by("example renderer")
        assert not doc.produced_by("Other Engine")


class TestWatermarkFindings:
    def test_clean_document_has_none(self):
        assert PdfTextView.parse(invoice_pdf()).watermark_findings() == []

    def test_diagonal_banner_is_found(self):
        assert watermark_findings(_stamped("EVALUATION COPY")) == ["evaluation copy"]

    def test_glyph_by_glyph_banner_is_found(self):
        doc = PdfTextView.parse(_stamped("Evaluation Only", per_glyph=True))
        assert doc.watermark_findings() == ["evaluation only"]

    def test_custom_phrases_in_given_order(self):
        data = _stamped("SPECIMEN - NOT VALID")
        assert watermark_findings(data, ["not valid", "specimen", "absent"]) == [
            "not valid",
            "specimen",
        ]

    def test_blank_phrases_never_match(self):
        assert watermark_findings(invoice_pdf(), ["", "  "]) == []

    def test_phrases_from_environment(self, monkeypatch):
        assert overlay_phrases({}) == DEFAULT_OVERLAY_PHRASES
        assert overlay_phrases({PHRASES_KEY: " trial , preview ,"}) == ("trial", "preview")
        monkeypatch.setenv(PHRASES_KEY, "body line 3")
        assert watermark_findings(invoice_pdf()) == []
        assert watermark_findings(_stamped("x")) == ["body line 3"]


def test_squash_drops_all_whitespace_and_case():
    assert squash(" E v A\nL\t") == "eval"
