"""Tests for ElementSpec, FormSpec, PageSpec models."""

from web_discovery.parser.models import ElementSpec, FormSpec, PageSpec


def _el(element_type="text", name="field", label="", required=False, placeholder="", aria_label=""):
    return ElementSpec(
        tag="input",
        element_type=element_type,
        selector=f"[name={name!r}]",
        label=label,
        placeholder=placeholder,
        name=name,
        element_id="",
        required=required,
        input_pattern=None,
        aria_label=aria_label,
        text_content="",
    )


class TestElementSpec:
    def test_display_name_prefers_label(self):
        el = _el(label="Email address", name="email")
        assert el.display_name == "Email address"

    def test_display_name_falls_back_to_placeholder(self):
        el = _el(placeholder="Enter email", name="email")
        assert el.display_name == "Enter email"

    def test_display_name_falls_back_to_name(self):
        el = _el(name="username")
        assert el.display_name == "username"

    def test_display_name_aria_label(self):
        el = _el(aria_label="Search input", name="q")
        assert el.display_name == "Search input"

    def test_is_form_field_text(self):
        assert _el(element_type="text").is_form_field()

    def test_is_form_field_select(self):
        assert _el(element_type="select").is_form_field()

    def test_is_form_field_checkbox(self):
        assert _el(element_type="checkbox").is_form_field()

    def test_is_form_field_submit_false(self):
        assert not _el(element_type="submit").is_form_field()

    def test_is_form_field_button_false(self):
        assert not _el(element_type="button").is_form_field()

    def test_is_submit(self):
        assert _el(element_type="submit").is_submit()

    def test_is_link_false_for_input(self):
        assert not _el(element_type="text").is_link()


class TestFormSpec:
    def _form(self, fields=None, required_names=None):
        required_names = required_names or []
        fields = fields or [
            _el(element_type="text", name="a", required="a" in required_names),
            _el(element_type="email", name="b", required="b" in required_names),
        ]
        return FormSpec(
            action="/submit",
            method="POST",
            name="test_form",
            fields=fields,
            submit_selector="button[type=submit]",
        )

    def test_required_fields_empty_when_none_required(self):
        form = self._form()
        assert form.required_fields == []

    def test_required_fields(self):
        form = FormSpec(
            action="",
            method="POST",
            name="",
            fields=[
                _el(name="x", required=True),
                _el(name="y", required=False),
            ],
            submit_selector="",
        )
        assert len(form.required_fields) == 1
        assert form.required_fields[0].name == "x"

    def test_optional_fields(self):
        form = FormSpec(
            action="",
            method="POST",
            name="",
            fields=[
                _el(name="x", required=True),
                _el(name="y", required=False),
            ],
            submit_selector="",
        )
        assert len(form.optional_fields) == 1
        assert form.optional_fields[0].name == "y"


class TestPageSpec:
    def _page(self):
        form = FormSpec(
            action="/",
            method="POST",
            name="",
            fields=[_el(name="email"), _el(element_type="submit", name="")],
            submit_selector="button",
        )
        return PageSpec(url="http://example.com", title="Home", forms=[form])

    def test_all_form_fields_returns_all_fields(self):
        spec = self._page()
        fields = spec.all_form_fields
        assert len(fields) >= 1

    def test_all_elements_includes_standalone(self):
        spec = self._page()
        btn = _el(element_type="button", name="go")
        spec.standalone_elements = [btn]
        assert btn in spec.all_elements

    def test_to_dict_has_url(self):
        spec = self._page()
        d = spec.to_dict()
        assert d["url"] == "http://example.com"
        assert "forms" in d
