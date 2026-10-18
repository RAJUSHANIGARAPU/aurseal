"""A downloaded PDF parsed once: header check, producer, page count and text.

A document pipeline answers 200 with bytes even when it rendered the wrong
thing: an HTML error page, a document from an unlicensed or fallback renderer
stamped with an evaluation banner, or output from a different engine than the
one deployed. :class:`PdfTextView` reads the bytes once and answers those
questions without each test re-parsing.

Banner detection (:meth:`PdfTextView.watermark_findings`) compares
whitespace-squashed, case-folded text: renderers often place each glyph of an
overlay separately or draw it diagonally across the body, so extraction
splits the words ("E V A L U A T I O N"). Both pypdf's layout and plain
extraction are searched, because each keeps a different kind of overlay
intact.

Needs ``pypdf`` (see :mod:`oracles.pdf_text`).
"""

from __future__ import annotations

import io
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass

from oracles import pdf_text

PDF_MAGIC = b"%PDF-"
PHRASES_KEY = "PDF_OVERLAY_PHRASES"

#: Overlay phrases reported by default. Override per call or with ``PDF_OVERLAY_PHRASES``
#: (comma-separated).
DEFAULT_OVERLAY_PHRASES: tuple[str, ...] = (
    "evaluation only",
    "evaluation copy",
    "evaluation version",
    "created with an evaluation",
    "not for production use",
    "specimen",
    "draft copy",
)

_WS = re.compile(r"\s+")


class NotAPdf(ValueError):
    """The bytes are not a PDF; the message shows what they start with."""


def is_pdf(data: bytes) -> bool:
    """Whether ``data`` starts with the PDF header (leading whitespace tolerated)."""
    return data.lstrip()[:5] == PDF_MAGIC


def squash(text: str) -> str:
    """Case-folded text with every whitespace character removed."""
    return _WS.sub("", text).casefold()


def overlay_phrases(environ: dict[str, str] | None = None) -> tuple[str, ...]:
    """Phrases from ``PDF_OVERLAY_PHRASES`` when set, else :data:`DEFAULT_OVERLAY_PHRASES`."""
    raw = (os.environ if environ is None else environ).get(PHRASES_KEY, "")
    configured = tuple(p.strip() for p in raw.split(",") if p.strip())
    return configured or DEFAULT_OVERLAY_PHRASES


@dataclass(frozen=True)
class PdfTextView:
    size: int
    producer: str | None
    creator: str | None
    page_count: int
    text: str
    """Layout-mode text (see :func:`oracles.pdf_text.extract_text`)."""
    plain_text: str
    """pypdf's plain extraction: one run per line, kept for overlays layout mode breaks up."""

    @classmethod
    def parse(cls, data: bytes) -> PdfTextView:
        if not is_pdf(data):
            head = data[:40].decode("latin-1", errors="replace")
            raise NotAPdf(f"not a PDF ({len(data)} bytes, starts with {head!r})")
        pypdf = pdf_text._pypdf()
        reader = pypdf.PdfReader(io.BytesIO(data))
        meta = reader.metadata or {}
        plain = "\n".join((page.extract_text() or "") for page in reader.pages)
        return cls(
            size=len(data),
            producer=_meta(meta, "/Producer"),
            creator=_meta(meta, "/Creator"),
            page_count=len(reader.pages),
            text=pdf_text.extract_text(data),
            plain_text=plain,
        )

    def watermark_findings(self, phrases: Iterable[str] | None = None) -> list[str]:
        """The configured overlay phrases present in the document, in the order given."""
        views = (squash(self.text), squash(self.plain_text))
        wanted = overlay_phrases() if phrases is None else tuple(phrases)
        return [p for p in wanted if squash(p) and any(squash(p) in v for v in views)]

    def produced_by(self, expected: str) -> bool:
        """Whether the producer (or creator) names ``expected`` (case-insensitive substring)."""
        needle = expected.casefold()
        return any(v and needle in v.casefold() for v in (self.producer, self.creator))

    def describe(self) -> str:
        return (
            f"PDF {self.size} bytes, {self.page_count} page(s), "
            f"producer={self.producer!r}, creator={self.creator!r}"
        )


def _meta(meta, key: str) -> str | None:
    value = meta.get(key)
    return str(value) if value is not None else None


def watermark_findings(data: bytes, phrases: Iterable[str] | None = None) -> list[str]:
    """Shortcut for ``PdfTextView.parse(data).watermark_findings(phrases)``."""
    return PdfTextView.parse(data).watermark_findings(phrases)
