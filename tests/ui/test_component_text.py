"""Pure string rules under the UI components: label echoes, exact paths, amounts, dates."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

import pytest

from ui.components.date_entry import LOCALE_FORMATS, format_date
from ui.components.exact_attribute import identifies, path_test_id_pattern
from ui.components.field_text import (
    carries_no_value,
    is_label_echo_only,
    looks_like_validation_message,
    strip_label_echo,
)
from ui.components.response_capture import amount_at, find_amount_path, first_amount, iter_numbers
from ui.components.value_entry import amounts_equal, parse_amount


class TestLabelEcho:
    def test_an_empty_field_reads_as_nothing_not_as_its_label(self):
        assert strip_label_echo("Company type *", "Company type") == ""
        assert is_label_echo_only("Company type  ", "company TYPE")

    def test_every_occurrence_goes_including_one_inside_the_error(self):
        scraped = "Company type Company type has an invalid value"
        assert strip_label_echo(scraped, "Company type") == "has an invalid value"
        assert carries_no_value(scraped, "Company type")

    def test_word_boundaries_protect_the_value(self):
        """A blind strip of 'Land' would turn 'Netherland' into 'Nether'."""
        assert strip_label_echo("Land Netherland", "Land") == "Netherland"

    def test_labels_ending_in_punctuation_still_match(self):
        assert strip_label_echo("Amount (€) 57,25", "Amount (€)") == "57,25"

    def test_non_breaking_spaces_are_collapsed(self):
        assert strip_label_echo("Name  Ada Lovelace", "Name") == "Ada Lovelace"

    def test_a_real_value_carries_a_value(self):
        assert not carries_no_value("City Utrecht", "City")

    def test_validation_wording_is_matched_exactly_not_by_shape(self):
        assert looks_like_validation_message("Email is required")
        assert not looks_like_validation_message("Requires review by the team lead")
        assert looks_like_validation_message("veld is leeg", phrases=["is leeg"])

    @pytest.mark.parametrize("scraped", [None, "", "   "])
    def test_blank(self, scraped):
        assert carries_no_value(scraped, "Name")


class TestExactPath:
    PATH = "customer.email"

    @pytest.mark.parametrize(
        "value, expected",
        [
            ("customer.email", True),
            ("field-text-form.customer.email", True),
            ("input-customer.email", True),
            ("field-text-form.customer.email-error", False),
            ("customer.email-label", False),
            ("customer.emailAddress", False),
            ("customer.email.domain", False),
            ("xcustomer.email", False),
            ("", False),
        ],
    )
    def test_identifies(self, value, expected):
        assert identifies(value, self.PATH) is expected

    def test_the_locator_pattern_agrees_with_the_rule(self):
        pattern = path_test_id_pattern(self.PATH)
        for value in [
            "customer.email",
            "a.customer.email",
            "a-customer.email",
            "customer.email-error",
            "a.customer.emails",
        ]:
            assert bool(pattern.search(value)) is identifies(value, self.PATH), value

    def test_special_characters_are_escaped(self):
        assert re.escape("items[0].price") in path_test_id_pattern("items[0].price").pattern

    def test_empty_path_is_rejected(self):
        with pytest.raises(ValueError):
            path_test_id_pattern("")


class TestAmounts:
    @pytest.mark.parametrize(
        "text, expected",
        [
            ("100.00", "100.00"),
            ("57,25", "57.25"),
            ("1,234.56", "1234.56"),
            ("1.234,56", "1234.56"),
            ("€ 1.234,56", "1234.56"),
            ("$1,234", "1234"),
            ("1.234.567", "1234567"),
            ("12.5", "12.5"),
            ("-7,50", "-7.50"),
            ("7,50-", "-7.50"),
            ("1 234,5", "1234.5"),
        ],
    )
    def test_notations(self, text, expected):
        assert parse_amount(text) == Decimal(expected)

    @pytest.mark.parametrize("text", [None, "", "n/a", "1.2.3,4,5"])
    def test_unreadable(self, text):
        assert parse_amount(text) is None

    def test_equality_is_numeric_and_unreadable_is_never_equal(self):
        assert amounts_equal("57,25", "57.25")
        assert not amounts_equal("5725100.00", "57.25")
        assert not amounts_equal("", "")


class TestDates:
    @pytest.mark.parametrize(
        "locale, expected",
        [
            ("iso", "2026-03-09"),
            ("en_US", "03/09/2026"),
            ("en_GB", "09/03/2026"),
            ("nl_NL", "09-03-2026"),
            ("de_DE", "09.03.2026"),
        ],
    )
    def test_formats(self, locale, expected):
        assert format_date(date(2026, 3, 9), locale) == expected

    def test_unknown_locale_lists_the_known_ones(self):
        with pytest.raises(ValueError, match="known"):
            format_date(date(2026, 1, 1), "xx_XX")
        assert "iso" in LOCALE_FORMATS


class TestResponseDocuments:
    DOC = {
        "totals": {"gross": 121.0, "net": "100", "flag": True},
        "items": [{"price": 60.505}, {"price": 40}],
    }

    def test_paths(self):
        assert amount_at(self.DOC, "totals/gross") == Decimal("121.00")
        assert amount_at(self.DOC, "totals/net") == Decimal("100.00")
        assert amount_at(self.DOC, "items/0/price") == Decimal("60.51")
        assert amount_at(self.DOC, "items/9/price") is None
        assert amount_at(self.DOC, "totals/flag") is None

    def test_numbers_are_walked_in_document_order_skipping_booleans(self):
        assert [p for p, _ in iter_numbers(self.DOC)] == [
            "totals/gross",
            "items/0/price",
            "items/1/price",
        ]

    def test_find_which_field_carries_a_figure(self):
        assert find_amount_path(self.DOC, Decimal("40.00")) == "items/1/price"
        assert find_amount_path(self.DOC, Decimal("999")) is None

    def test_first_of_several_candidate_paths(self):
        assert first_amount(self.DOC, ["totals/vat", "totals/net"]) == (
            "totals/net",
            Decimal("100.00"),
        )
        assert first_amount(self.DOC, ["nope"]) is None
