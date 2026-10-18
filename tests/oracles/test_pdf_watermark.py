"""Both sides of the overlay-banner oracle, plus refusal of non-PDF bytes."""

from __future__ import annotations

import pytest

pytest.importorskip("pypdf")

from oracles.pdf_text_view import PdfTextView  # noqa: E402
from oracles.pdf_watermark import (  # noqa: E402
    NotAPdf,
    OverlayFound,
    assert_unwatermarked,
    find_overlay_phrases,
)
from tests.oracles.pdf_fixtures import TextRun, build_pdf, invoice_pdf  # noqa: E402


def _render(watermark: str | None) -> bytes:
    runs = [TextRun(60, 760, "Order ORD-42"), TextRun(60, 742, "Total due  EUR 99.00")]
    if watermark:
        runs += [TextRun(80 + 20 * i, 200 + 20 * i, c, 34, 45) for i, c in enumerate(watermark)]
    return build_pdf([runs], producer="Example Renderer 2.1")


def test_clean_render_passes_and_returns_the_document():
    doc = assert_unwatermarked(_render(None), expected_producer="Example Renderer")
    assert isinstance(doc, PdfTextView) and doc.page_count == 1


def test_stamped_render_fails_naming_each_phrase():
    with pytest.raises(OverlayFound) as exc:
        assert_unwatermarked(_render("DRAFT COPY - SPECIMEN"))
    assert exc.value.phrases == ["specimen", "draft copy"]
    assert "Example Renderer 2.1" in str(exc.value)


def test_each_default_phrase_is_detected_individually():
    for phrase in ["Evaluation only", "Created with an evaluation", "Not for production use"]:
        assert find_overlay_phrases(_render(phrase)) == [phrase.lower()], phrase


def test_wrong_producer_fails_even_without_banner():
    with pytest.raises(AssertionError, match="expected a render by 'Other Engine'"):
        assert_unwatermarked(_render(None), expected_producer="Other Engine")


def test_parsed_document_is_accepted():
    doc = PdfTextView.parse(invoice_pdf())
    assert find_overlay_phrases(doc, ["sample street"]) == ["sample street"]


@pytest.mark.parametrize("data", [b"", b'{"error": "render failed"}', b"PK\x03\x04zip"])
def test_non_pdf_bytes_are_refused_not_read_as_clean(data):
    with pytest.raises(NotAPdf):
        assert_unwatermarked(data)
