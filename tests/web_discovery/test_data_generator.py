"""Tests for DataGenerator."""

import re

import pytest

from web_discovery.data_generator.generator import DataGenerator
from web_discovery.parser.models import ElementSpec


def _el(element_type="text", name="field", label="", placeholder="", aria_label=""):
    return ElementSpec(
        tag="input",
        element_type=element_type,
        selector=f"[name={name!r}]",
        label=label,
        placeholder=placeholder,
        name=name,
        element_id="",
        required=False,
        input_pattern=None,
        aria_label=aria_label,
        text_content="",
    )


@pytest.fixture()
def gen():
    return DataGenerator(seed=42)


class TestEmailGeneration:
    def test_email_type_generates_email(self, gen):
        val = gen.generate_for_field(_el(element_type="email"))
        assert "@" in val

    def test_email_label_generates_email(self, gen):
        val = gen.generate_for_field(_el(label="Email Address"))
        assert "@" in val

    def test_email_name_hint_generates_email(self, gen):
        val = gen.generate_for_field(_el(name="email"))
        assert "@" in val


class TestPasswordGeneration:
    def test_password_type_returns_string(self, gen):
        val = gen.generate_for_field(_el(element_type="password", name="pwd"))
        assert isinstance(val, str)
        assert len(val) >= 8

    def test_password_label_triggers_password_gen(self, gen):
        val = gen.generate_for_field(_el(label="Password"))
        assert len(val) >= 8


class TestPhoneGeneration:
    def test_tel_type(self, gen):
        val = gen.generate_for_field(_el(element_type="tel"))
        assert val


class TestDateGeneration:
    def test_date_type_returns_formatted_date(self, gen):
        val = gen.generate_for_field(_el(element_type="date"))
        assert re.match(r"\d{4}-\d{2}-\d{2}", val)


class TestNumberGeneration:
    def test_number_type_returns_int_string(self, gen):
        val = gen.generate_for_field(_el(element_type="number"))
        assert val.isdigit()

    def test_count_label_returns_number(self, gen):
        val = gen.generate_for_field(_el(label="Count"))
        assert val.isdigit()


class TestCheckboxGeneration:
    def test_checkbox_returns_true(self, gen):
        val = gen.generate_for_field(_el(element_type="checkbox"))
        assert val == "true"


class TestFallback:
    def test_unknown_type_returns_non_empty_string(self, gen):
        val = gen.generate_for_field(_el(element_type="text", name="custom_field"))
        assert isinstance(val, str)
        assert len(val) > 0

    def test_textarea_returns_sentence(self, gen):
        val = gen.generate_for_field(_el(element_type="textarea", name="bio"))
        assert len(val) > 5

    def test_url_type_returns_url(self, gen):
        val = gen.generate_for_field(_el(element_type="url"))
        assert val.startswith("http")


class TestNoFaker:
    def test_generates_fallback_without_faker(self, monkeypatch):
        import web_discovery.data_generator.generator as mod

        orig = mod._faker
        mod._faker = None
        try:
            g = DataGenerator()
            g._f = None
            val = g.generate_for_field(_el(element_type="email"))
            assert "@" in val
        finally:
            mod._faker = orig
