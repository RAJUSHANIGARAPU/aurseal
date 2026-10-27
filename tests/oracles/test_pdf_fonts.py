"""PDF font inventory and the produced-by-this-run control, on PDFs generated here.

The writer below emits raw PDF objects (stdlib only), so each fixture carries exactly the
font structures under test: an embedded subset TrueType font, a standard-14 font that is
never embedded, a composite (Type0) font, and a Form XObject with its own resources.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from oracles import pdf_fonts
from oracles.pdf_fonts import (
    FontReport,
    assert_fonts,
    family_of,
    fonts,
    parse_pdf_date,
    produced_by_run,
    produced_since,
    uses_family,
)
from oracles.pdf_text import available

pytestmark = pytest.mark.skipif(not available(), reason="pypdf is not installed")

STANDARD = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"


def _pdf(objects: dict[int, bytes], *, info: bytes | None = None) -> bytes:
    """Objects 1 (catalog) and 2 (pages) must be present; ``info`` adds an Info dictionary."""
    numbers = sorted(objects)
    if info is not None:
        numbers.append(max(numbers) + 1)
        objects = {**objects, numbers[-1]: info}
    out = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for number in numbers:
        offsets[number] = len(out)
        out += f"{number} 0 obj\n".encode() + objects[number] + b"\nendobj\n"
    xref = len(out)
    size = numbers[-1] + 1
    out += f"xref\n0 {size}\n0000000000 65535 f \n".encode()
    for number in range(1, size):
        kind = "n" if number in offsets else "f"
        out += f"{offsets.get(number, 0):010d} 00000 {kind} \n".encode()
    trailer = f"<< /Size {size} /Root 1 0 R"
    if info is not None:
        trailer += f" /Info {numbers[-1]} 0 R"
    out += f"trailer\n{trailer} >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def _stream(body: bytes, extra: bytes = b"") -> bytes:
    return b"<< /Length %d %s >>\nstream\n" % (len(body), extra) + body + b"\nendstream"


def _one_page(font_dict: bytes, extra: dict[int, bytes] | None = None, **kw) -> bytes:
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources 4 0 R "
        b"/Contents 5 0 R >>",
        4: b"<< /Font " + font_dict + b" >>",
        5: _stream(b"BT /F1 10 Tf 10 10 Td (x) Tj ET"),
        **(extra or {}),
    }
    return _pdf(objects, **kw)


def embedded_subset_pdf(**kw) -> bytes:
    """One subset-prefixed TrueType font with a FontFile2 stream."""
    return _one_page(
        b"<< /F1 6 0 R >>",
        {
            6: b"<< /Type /Font /Subtype /TrueType /BaseFont /ABCDEF+SegoeUI-Bold "
            b"/FontDescriptor 7 0 R >>",
            7: b"<< /Type /FontDescriptor /FontName /ABCDEF+SegoeUI-Bold /FontFile2 8 0 R >>",
            8: _stream(b"\x00\x01\x00\x00fake-font-program"),
        },
        **kw,
    )


def standard_pdf() -> bytes:
    """Helvetica, one of the standard 14: referenced, never embedded."""
    return _one_page(b"<< /F1 6 0 R >>", {6: STANDARD})


def mixed_pdf() -> bytes:
    """A Type0 font with an embedded descendant, plus a Form XObject (drawn twice) whose
    own resources hold an unembedded font and share the page's resource dictionary."""
    return _pdf(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Kids [3 0 R 13 0 R] /Count 2 >>",
            3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources 4 0 R "
            b"/Contents 5 0 R >>",
            4: b"<< /Font << /F1 6 0 R >> /XObject << /X1 9 0 R /X2 9 0 R >> >>",
            5: _stream(b"/X1 Do /X2 Do"),
            6: b"<< /Type /Font /Subtype /Type0 /BaseFont /QWERTY+Brand#20Sans-Regular "
            b"/Encoding /Identity-H /DescendantFonts [7 0 R] >>",
            7: b"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /QWERTY+Brand#20Sans-Regular "
            b"/FontDescriptor 8 0 R >>",
            8: b"<< /Type /FontDescriptor /FontName /QWERTY+Brand#20Sans-Regular "
            b"/FontFile2 12 0 R >>",
            9: _stream(
                b"BT /F2 10 Tf (y) Tj ET",
                b"/Type /XObject /Subtype /Form /BBox [0 0 10 10] /Resources 10 0 R",
            ),
            10: b"<< /Font << /F2 11 0 R >> /XObject << /Back 9 0 R >> >>",
            11: b"<< /Type /Font /Subtype /TrueType /BaseFont /Arial,Bold >>",
            12: _stream(b"fake"),
            13: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources 4 0 R >>",
        }
    )


def test_embedded_subset_font_has_its_prefix_removed():
    report = fonts(embedded_subset_pdf())
    assert report.fonts == ("SegoeUI-Bold",)
    assert report.not_embedded == ()
    assert [(f.name, f.embedded, f.subtype) for f in report] == [("SegoeUI-Bold", True, "TrueType")]


def test_standard_14_font_is_not_embedded():
    report = fonts(standard_pdf())
    assert report.fonts == report.not_embedded == ("Helvetica",)


def test_type0_descendant_and_form_xobjects_are_walked_once():
    report = fonts(mixed_pdf())
    assert report.fonts == ("Arial,Bold", "Brand Sans-Regular")
    assert report.not_embedded == ("Arial,Bold",)
    assert len(report) == 2  # the shared resources and the self-referencing form count once


@pytest.mark.parametrize(
    ("name", "family", "expected"),
    [
        ("SegoeUI-Bold", "Segoe UI", True),
        ("ABCDEF+Segoe UI,Italic", "segoe ui", True),
        ("Arial,Bold", "Arial", True),
        ("SegoeUIEmoji", "Segoe UI", False),
        ("Helvetica", "Arial", False),
    ],
)
def test_family_comparison(name, family, expected):
    report = FontReport((pdf_fonts.Font(name, True),))
    assert uses_family(report, family) is expected


def test_family_key():
    assert family_of("ABCDEF+Segoe UI-Semibold") == "segoeui"


def test_oracle_holds_for_the_template_family_fully_embedded():
    report = assert_fonts(embedded_subset_pdf(), "Segoe UI")
    assert report.fonts == ("SegoeUI-Bold",)


def test_oracle_names_a_substituted_family():
    with pytest.raises(AssertionError, match=r"does not use font family 'Segoe UI'.*Helvetica"):
        assert_fonts(standard_pdf(), "Segoe UI")


def test_oracle_names_an_unembedded_font_unless_waived():
    with pytest.raises(AssertionError, match=r"not embedded.*Helvetica"):
        assert_fonts(standard_pdf(), "Helvetica")
    assert assert_fonts(standard_pdf(), "Helvetica", all_embedded=False).fonts == ("Helvetica",)


def test_without_pypdf_the_error_names_the_package(monkeypatch):
    def missing():
        raise pdf_fonts.PdfSupportMissing("pip install pypdf")

    monkeypatch.setattr(pdf_fonts, "_pypdf", missing)
    with pytest.raises(pdf_fonts.PdfSupportMissing, match="pypdf"):
        fonts(standard_pdf())


# ---------------------------------------------------------------- produced by this run

RUN_START = datetime(2031, 10, 4, 8, 0, tzinfo=timezone.utc)


def test_pdf_dates_parse_with_and_without_offsets():
    assert parse_pdf_date("D:20311004101500+02'00'") == RUN_START + timedelta(minutes=15)
    assert parse_pdf_date("D:20311004081500Z") == RUN_START + timedelta(minutes=15)
    assert parse_pdf_date("D:2031") == datetime(2031, 1, 1, tzinfo=timezone.utc)
    assert parse_pdf_date("yesterday") is None


def test_produced_since_allows_clock_skew_only():
    assert produced_since(RUN_START - timedelta(seconds=90), RUN_START)
    assert not produced_since(RUN_START - timedelta(minutes=3), RUN_START)
    assert not produced_since(None, RUN_START)
    naive = datetime(2031, 10, 4, 8, 5)
    assert produced_since(naive, RUN_START.replace(tzinfo=None))


def test_a_document_stamped_during_the_run_is_this_runs():
    data = embedded_subset_pdf(info=b"<< /CreationDate (D:20311004081000Z) >>")
    ok, why = produced_by_run(data, RUN_START)
    assert ok and why.startswith("created 2031-10-04T08:10")


def test_an_older_stored_document_is_not_evidence():
    data = embedded_subset_pdf(
        info=b"<< /CreationDate (D:20311003080000Z) /ModDate (D:20311003090000Z) >>"
    )
    ok, why = produced_by_run(data, RUN_START)
    assert not ok and "earlier render" in why
    # Unless the run created that document id itself.
    ok, why = produced_by_run(data, RUN_START, document_id="doc-9", created_ids={"doc-9"})
    assert ok and "doc-9" in why


def test_a_document_without_dates_is_not_evidence():
    ok, why = produced_by_run(standard_pdf(), RUN_START)
    assert not ok and "no date" in why
