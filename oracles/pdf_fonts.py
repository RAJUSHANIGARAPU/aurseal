"""PDF font inventory: which font families a document uses, and whether each is embedded.

A document rendered after a runtime or image change can look right in the text and still
be wrong on paper: the renderer could not find the template's font and substituted
another, or referenced a font without embedding it (the reader's machine then picks a
substitute). Both are visible in the PDF's font resources, so this reads them:

* :func:`fonts` walks every page's ``/Resources /Font`` and recurses into Form XObjects
  (each resource dictionary is visited once, so a shared one is not counted twice). A
  subset prefix (``ABCDEF+``) is removed from the name. A font is *embedded* when its
  descriptor has ``/FontFile``, ``/FontFile2`` or ``/FontFile3``; for a composite
  (``Type0``) font the descendant font's descriptor is the one that counts. A ``Type3``
  font draws its glyphs from the document itself and is embedded by definition.
* :func:`uses_family` compares families: the part of the name before the first ``-`` or
  ``,``, ignoring spaces and case (``SegoeUI-Bold`` is family ``Segoe UI``).
* :func:`assert_fonts` is the oracle: the document uses ``family`` and, by default, no
  font is left unembedded.

Fonts alone do not prove the document is new: a stored file from an earlier render would
pass. :func:`produced_since` and :func:`produced_by_run` are the control: the document's
creation (or modification) time is not before the run started, minus clock skew, or its
id is one this run created.

``pypdf`` is imported lazily, exactly as in :mod:`oracles.pdf_text`.
"""

from __future__ import annotations

import io
import re
from collections.abc import Collection, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from oracles.pdf_text import PdfSupportMissing, _pypdf

_SUBSET = re.compile(r"^[A-Z]{6}\+")
_FONT_FILES = ("/FontFile", "/FontFile2", "/FontFile3")
_PDF_DATE = re.compile(
    r"^D:(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?(?:([Zz+-])(\d{2})?'?(\d{2})?'?)?"
)
DEFAULT_SKEW = timedelta(minutes=2)

Source = str | Path | bytes


@dataclass(frozen=True)
class Font:
    """One font as the document references it."""

    name: str  # without the subset prefix
    embedded: bool
    subtype: str = ""

    @property
    def family(self) -> str:
        return family_of(self.name)


@dataclass(frozen=True)
class FontReport:
    """Every distinct font of a document; ``fonts`` and ``not_embedded`` are sorted names."""

    entries: tuple[Font, ...] = ()

    @property
    def fonts(self) -> tuple[str, ...]:
        return tuple(sorted({f.name for f in self.entries}))

    @property
    def not_embedded(self) -> tuple[str, ...]:
        return tuple(sorted({f.name for f in self.entries if not f.embedded}))

    def __iter__(self) -> Iterator[Font]:
        return iter(self.entries)

    def __len__(self) -> int:
        return len(self.entries)


def strip_subset(name: str) -> str:
    """``ABCDEF+SegoeUI-Bold`` -> ``SegoeUI-Bold``."""
    return _SUBSET.sub("", name.lstrip("/"))


def family_of(name: str) -> str:
    """Comparable family key: before the first ``-`` or ``,``, spaces removed, lower case."""
    base = re.split(r"[-,]", strip_subset(name), maxsplit=1)[0]
    return base.replace(" ", "").lower()


def uses_family(report: FontReport, family: str) -> bool:
    wanted = family_of(family)
    return any(f.family == wanted for f in report)


def _resolve(obj: Any) -> Any:
    return obj.get_object() if hasattr(obj, "get_object") else obj


def _key(obj: Any, resolved: Any) -> object:
    ref = getattr(obj, "indirect_reference", None) or getattr(resolved, "indirect_reference", None)
    if ref is not None:
        return ("ref", ref.idnum, ref.generation)
    return ("obj", id(resolved))


def _font(font: Any) -> Font:
    subtype = str(font.get("/Subtype", ""))
    if subtype == "/Type3":
        return Font(strip_subset(str(font.get("/Name", "Type3"))), True, "Type3")
    name = strip_subset(str(font.get("/BaseFont", "(unnamed)")))
    holder = font
    if subtype == "/Type0":
        descendants = _resolve(font.get("/DescendantFonts")) or []
        holder = _resolve(descendants[0]) if len(descendants) else {}
    descriptor = _resolve(holder.get("/FontDescriptor")) if holder else None
    embedded = bool(descriptor) and any(key in descriptor for key in _FONT_FILES)
    return Font(name, embedded, subtype.lstrip("/"))


def _walk(resources: Any, seen: set[object], found: dict[tuple[str, bool], Font]) -> None:
    resolved = _resolve(resources)
    if not resolved:
        return
    key = _key(resources, resolved)
    if key in seen:
        return
    seen.add(key)
    for ref in (_resolve(resolved.get("/Font")) or {}).values():
        font = _font(_resolve(ref))
        found.setdefault((font.name, font.embedded), font)
    for ref in (_resolve(resolved.get("/XObject")) or {}).values():
        xobject = _resolve(ref)
        if xobject.get("/Subtype") == "/Form" and "/Resources" in xobject:
            _walk(xobject["/Resources"], seen, found)


def _reader(source: Source):
    pypdf = _pypdf()
    data = source if isinstance(source, bytes) else Path(source).read_bytes()
    return pypdf.PdfReader(io.BytesIO(data))


def fonts(source: Source) -> FontReport:
    """The document's fonts, from every page and every Form XObject they draw."""
    reader = _reader(source)
    seen: set[object] = set()
    found: dict[tuple[str, bool], Font] = {}
    for page in reader.pages:
        if "/Resources" in page:
            _walk(page["/Resources"], seen, found)
    ordered = sorted(found.values(), key=lambda f: (f.name, f.embedded))
    return FontReport(tuple(ordered))


def font_findings(report: FontReport, family: str, *, all_embedded: bool = True) -> list[str]:
    """What is wrong with the fonts; ``[]`` when the oracle holds."""
    findings = []
    if not uses_family(report, family):
        findings.append(
            f"the document does not use font family {family!r}; it uses {list(report.fonts)}"
        )
    if all_embedded and report.not_embedded:
        findings.append(
            f"font(s) not embedded (a reader substitutes them): {list(report.not_embedded)}"
        )
    return findings


def assert_fonts(
    source: Source | FontReport, family: str, *, all_embedded: bool = True
) -> FontReport:
    """The document uses ``family`` and (by default) embeds every font; else AssertionError."""
    report = source if isinstance(source, FontReport) else fonts(source)
    findings = font_findings(report, family, all_embedded=all_embedded)
    if findings:
        raise AssertionError("; ".join(findings))
    return report


# ---------------------------------------------------------------- produced by this run


def parse_pdf_date(text: str) -> datetime | None:
    """``D:20311004101500+02'00'`` -> an aware datetime (UTC when no offset); None if invalid."""
    match = _PDF_DATE.match(str(text).strip())
    if not match:
        return None
    year, month, day, hour, minute, second, sign, off_h, off_m = match.groups()
    tz = timezone.utc
    if sign in ("+", "-"):
        offset = timedelta(hours=int(off_h or 0), minutes=int(off_m or 0))
        tz = timezone(offset if sign == "+" else -offset)
    try:
        return datetime(
            int(year),
            int(month or 1),
            int(day or 1),
            int(hour or 0),
            int(minute or 0),
            int(second or 0),
            tzinfo=tz,
        )
    except ValueError:
        return None


def document_times(source: Source) -> tuple[datetime | None, datetime | None]:
    """``(creation, modification)`` from the document information dictionary."""
    info = _reader(source).metadata or {}
    return (
        parse_pdf_date(info.get("/CreationDate", "")) if info else None,
        parse_pdf_date(info.get("/ModDate", "")) if info else None,
    )


def produced_since(
    created_at: datetime | None, run_start: datetime, skew: timedelta = DEFAULT_SKEW
) -> bool:
    """True when ``created_at`` is not before ``run_start - skew``; a missing time is False."""
    if created_at is None:
        return False
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    if run_start.tzinfo is None:
        run_start = run_start.replace(tzinfo=timezone.utc)
    return created_at >= run_start - skew


def produced_by_run(
    source: Source,
    run_start: datetime,
    *,
    skew: timedelta = DEFAULT_SKEW,
    document_id: object = None,
    created_ids: Collection[object] = (),
) -> tuple[bool, str]:
    """Whether this run produced the document, and the evidence either way.

    Proven by its id (one this run created) or by its creation or modification time.
    """
    if document_id is not None and document_id in created_ids:
        return True, f"document {document_id} was created by this run"
    created, modified = document_times(source)
    for label, moment in (("created", created), ("modified", modified)):
        if produced_since(moment, run_start, skew):
            return True, f"{label} {moment.isoformat()}, run started {run_start.isoformat()}"
    shown = [m.isoformat() for m in (created, modified) if m is not None] or ["no date"]
    return False, (
        f"document carries {', '.join(shown)}, before the run start {run_start.isoformat()} "
        f"minus {skew}: a stored file from an earlier render is no evidence of this one"
    )


__all__ = [
    "DEFAULT_SKEW",
    "Font",
    "FontReport",
    "PdfSupportMissing",
    "assert_fonts",
    "document_times",
    "family_of",
    "font_findings",
    "fonts",
    "parse_pdf_date",
    "produced_by_run",
    "produced_since",
    "strip_subset",
    "uses_family",
]
