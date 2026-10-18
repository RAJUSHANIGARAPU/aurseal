"""Assert a rendered document carries no overlay banner (evaluation, draft, specimen ...).

A renderer running without its licence, or in a preview mode, still answers
200 with a valid PDF; only the page shows the problem, often as a diagonal
banner over the body. These helpers turn :class:`oracles.pdf_text_view.PdfTextView`
into an assertion with both sides covered: a clean document passes, a stamped
one fails naming each phrase found, and bytes that are not a PDF are refused
rather than read as "no banner found".
"""

from __future__ import annotations

from collections.abc import Iterable

from oracles.pdf_text_view import NotAPdf, PdfTextView

__all__ = ["NotAPdf", "OverlayFound", "assert_unwatermarked", "find_overlay_phrases"]


class OverlayFound(AssertionError):
    """The document carries one or more overlay phrases."""

    def __init__(self, document: PdfTextView, phrases: list[str]) -> None:
        super().__init__(f"overlay phrase(s) {phrases} found in {document.describe()}")
        self.document = document
        self.phrases = phrases


def _document(source: bytes | PdfTextView) -> PdfTextView:
    return source if isinstance(source, PdfTextView) else PdfTextView.parse(source)


def find_overlay_phrases(
    source: bytes | PdfTextView, phrases: Iterable[str] | None = None
) -> list[str]:
    """The overlay phrases present; raises :class:`NotAPdf` for non-PDF bytes."""
    return _document(source).watermark_findings(phrases)


def assert_unwatermarked(
    source: bytes | PdfTextView,
    phrases: Iterable[str] | None = None,
    *,
    expected_producer: str | None = None,
) -> PdfTextView:
    """Return the parsed document when it is clean; raise :class:`OverlayFound` when not.

    ``expected_producer`` additionally requires the producer/creator metadata
    to name that renderer, so output from a fallback engine is not mistaken
    for a clean render.
    """
    document = _document(source)
    found = document.watermark_findings(phrases)
    if found:
        raise OverlayFound(document, found)
    if expected_producer and not document.produced_by(expected_producer):
        raise AssertionError(f"expected a render by {expected_producer!r}: {document.describe()}")
    return document
