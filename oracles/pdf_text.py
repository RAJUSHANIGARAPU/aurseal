"""PDF text extraction behind an optional dependency.

Generated documents are judged on their text (see :mod:`oracles.document_findings`),
so extraction is the one part that needs a PDF library. ``pypdf`` is imported
lazily: the rest of :mod:`oracles` works without it, and calling
:func:`extract_text` without it raises :class:`PdfSupportMissing` naming the
package to install rather than an ``ImportError`` from deep inside a test.

Extraction uses pypdf's layout mode where available, so text on one visual
line stays on one line — label/value checks depend on that.
"""

from __future__ import annotations

import importlib
import io
from pathlib import Path

from oracles import amounts


class PdfSupportMissing(RuntimeError):
    """``pypdf`` is not installed."""


def _pypdf():
    try:
        return importlib.import_module("pypdf")
    except ImportError:
        raise PdfSupportMissing(
            "PDF extraction needs the optional 'pypdf' package: pip install pypdf"
        ) from None


def available() -> bool:
    try:
        _pypdf()
    except PdfSupportMissing:
        return False
    return True


def extract_text(source: str | Path | bytes) -> str:
    """All pages' text, pages separated by a newline."""
    pypdf = _pypdf()
    stream = io.BytesIO(source) if isinstance(source, bytes) else open(source, "rb")  # noqa: SIM115
    with stream:
        reader = pypdf.PdfReader(stream)
        pages = []
        for page in reader.pages:
            try:
                pages.append(page.extract_text(extraction_mode="layout") or "")
            # TypeError: older pypdf without layout mode.
            # KeyError: layout mode on a page without a content stream (a blank page).
            except (TypeError, KeyError):
                pages.append(page.extract_text() or "")
    return "\n".join(pages)


def extract_amounts(source: str | Path | bytes, locale: str = "nl_NL"):
    """Every amount in the document, in reading order."""
    return amounts.find_amounts(extract_text(source), locale)


def extract_dates(source: str | Path | bytes, locale: str = "nl_NL"):
    return amounts.find_dates(extract_text(source), locale)
