"""Tests for oracles.amounts, oracles.pdf_text and oracles.field_text."""

from __future__ import annotations

import sys
import types
from datetime import date
from decimal import Decimal as D

import pytest

from oracles import amounts, field_text, pdf_text


class TestParseAmount:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("1.234,56", "1234.56"),
            ("€ 1.234,56", "1234.56"),
            ("€1.234.567,00", "1234567.00"),
            ("102", "102"),
            ("102,5", "102.5"),
            ("-€ 12,00", "-12.00"),
            ("€ -12,00", "-12.00"),
            ("−7,25", "-7.25"),
            ("\u00a0€\u00a03,10 ", "3.10"),
        ],
    )
    def test_nl(self, text, expected):
        assert amounts.parse_amount(text, "nl_NL") == D(expected)

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("1,234.56", "1234.56"),
            ("$1,234.56", "1234.56"),
            ("-$12.00", "-12.00"),
            ("($12.00)", "-12.00"),
            ("0.99", "0.99"),
        ],
    )
    def test_en_us(self, text, expected):
        assert amounts.parse_amount(text, "en_US") == D(expected)

    @pytest.mark.parametrize("text", ["", "abc", "1.234,56 extra", "12,3,4"])
    def test_rejects_non_amounts(self, text):
        with pytest.raises(ValueError):
            amounts.parse_amount(text, "nl_NL")

    def test_same_text_reads_differently_per_locale(self):
        assert amounts.parse_amount("1.250", "nl_NL") == D("1250")
        assert amounts.parse_amount("1.25", "en_US") == D("1.25")

    def test_unknown_locale(self):
        with pytest.raises(ValueError, match="unsupported locale"):
            amounts.parse_amount("1", "xx_XX")

    def test_format_round_trips(self):
        for loc in ("nl_NL", "en_US", "de_DE"):
            for v in ("-1234.50", "0.07", "1000000.00"):
                assert amounts.parse_amount(amounts.format_amount(D(v), loc), loc) == D(v)


CREDIT_NOTE = """Credit note CN-0042          Date 15-03-2030
Period 01-03-2030 up to 31-03-2030
Subtotal              -€ 1.380,00
Tax                   -€ 289,80
To be refunded        € 1.669,80
"""


class TestFreeText:
    def test_finds_signed_amounts_and_ignores_dates(self):
        assert amounts.find_amounts(CREDIT_NOTE.splitlines()[2]) == [D("-1380.00")]
        assert D("-3") not in amounts.find_amounts(CREDIT_NOTE)

    def test_amount_after_label_keeps_the_leading_minus(self):
        assert amounts.amount_after_label(CREDIT_NOTE, "Tax") == D("-289.80")
        assert amounts.amount_after_label(CREDIT_NOTE, "To be refunded") == D("1669.80")
        assert amounts.amount_after_label(CREDIT_NOTE, "Discount") is None

    def test_dates_and_labelled_dates(self):
        assert amounts.find_dates(CREDIT_NOTE) == [
            date(2030, 3, 15),
            date(2030, 3, 1),
            date(2030, 3, 31),
        ]
        assert amounts.date_after_label(CREDIT_NOTE, "up to") == date(2030, 3, 31)
        assert amounts.find_dates("31-02-2030 and 29-02-2028") == [date(2028, 2, 29)]
        assert amounts.find_dates("03/31/2030", "en_US") == [date(2030, 3, 31)]
        assert amounts.parse_date("31.12.2030", "de_DE") == date(2030, 12, 31)

    def test_json_amounts_avoid_float_artefacts(self):
        assert amounts.parse_json_amount(0.1) == D("0.1")
        assert amounts.parse_json_amount("12.30") == D("12.30")
        assert amounts.parse_json_amount(None) is None
        with pytest.raises(ValueError):
            amounts.parse_json_amount("1,00")


class TestPdf:
    def test_missing_dependency_is_a_clear_error(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "pypdf", None)
        assert not pdf_text.available()
        with pytest.raises(pdf_text.PdfSupportMissing, match="pip install pypdf"):
            pdf_text.extract_text(b"%PDF")

    def test_extraction_goes_through_pypdf_layout_mode(self, monkeypatch, tmp_path):
        class Page:
            def __init__(self, text, layout=True):
                self.text, self.layout = text, layout

            def extract_text(self, **kw):
                if kw and not self.layout:
                    raise TypeError("no layout mode")
                return self.text

        class Reader:
            def __init__(self, stream):
                assert stream.read(4) == b"%PDF"
                self.pages = [Page("Total € 1.234,56"), Page("Due 01-04-2030", layout=False)]

        monkeypatch.setitem(sys.modules, "pypdf", types.SimpleNamespace(PdfReader=Reader))
        file = tmp_path / "doc.pdf"
        file.write_bytes(b"%PDF-1.7")
        assert pdf_text.extract_text(file) == "Total € 1.234,56\nDue 01-04-2030"
        assert pdf_text.extract_amounts(b"%PDF-x") == [D("1234.56")]
        assert pdf_text.extract_dates(b"%PDF-x") == [date(2030, 4, 1)]

    def test_real_pypdf_when_installed(self):
        pypdf = pytest.importorskip("pypdf")
        import io

        writer = pypdf.PdfWriter()
        writer.add_blank_page(width=72, height=72)
        buf = io.BytesIO()
        writer.write(buf)
        assert pdf_text.extract_text(buf.getvalue()).strip() == ""


class TestFieldText:
    def test_strips_every_word_bounded_echo_and_markers(self):
        assert field_text.strip_label_echo("Country * Country\u00a0Netherland", "country") == (
            "Netherland"
        )
        assert field_text.strip_label_echo("Land Netherland", "Land") == "Netherland"

    def test_label_with_punctuation_still_matches(self):
        assert field_text.strip_label_echo("Amount (€): 12,00", "Amount (€)") == "12,00"

    def test_echo_only_and_validation_residue_carry_no_value(self):
        assert field_text.is_label_echo_only(" Legal form * ", "Legal form")
        scraped = "Legal form Legal form has an invalid value"
        assert field_text.strip_label_echo(scraped, "Legal form") == "has an invalid value"
        assert field_text.carries_no_value(scraped, "Legal form")
        assert not field_text.carries_no_value("Legal form Partnership", "Legal form")
        assert field_text.carries_no_value(None, "x") and field_text.carries_no_value("", None)

    def test_validation_phrases_are_a_parameter(self):
        assert not field_text.looks_like_validation_message("Veld ontbreekt")
        assert field_text.carries_no_value("Name Veld ontbreekt", "Name", phrases=["ontbreekt"])
        assert not field_text.looks_like_validation_message("A required reading list")
