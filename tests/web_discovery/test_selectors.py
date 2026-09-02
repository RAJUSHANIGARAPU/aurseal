"""
Selector construction, including the values that used to produce broken output.

``dom_parser.py`` was at 0% coverage. Its entire product is selectors that get
written into generated test files, and they were built with Python's ``repr()``
— and, for ids, with no escaping at all. The failures are silent: a selector
that means the wrong thing still parses and still returns *an* element.

``_best_selector`` only ever calls ``get_attribute`` and ``inner_text`` on the
element, so the whole priority order is exercisable here with a stub — no
browser, no page, no network.
"""

from __future__ import annotations

import pytest

from web_discovery.parser.dom_parser import DomParser
from web_discovery.parser.selectors import (
    attribute_selector,
    css_string,
    id_selector,
    text_selector,
)


class StubElement:
    """Duck-types the two Playwright methods ``_best_selector`` uses."""

    def __init__(self, text: str = "", **attributes: str) -> None:
        self._attributes = {k.replace("_", "-"): v for k, v in attributes.items()}
        self._text = text

    def get_attribute(self, name: str):
        return self._attributes.get(name)

    def inner_text(self) -> str:
        return self._text


def best(tag: str = "button", text: str = "", **attributes) -> str:
    return DomParser()._best_selector(StubElement(text=text, **attributes), tag)


class TestIdEscaping:
    """
    The defect with the widest blast radius.

    ``f"#{id}"`` treats every id as a bare CSS identifier. Ids in real apps are
    routinely not: frameworks interpolate keys, and HTML5 permits almost
    anything.
    """

    def test_a_dot_in_an_id_no_longer_reads_as_a_class(self):
        selector = id_selector("user.name")
        assert selector != "#user.name"
        assert selector == '[id="user.name"]'

    def test_an_id_starting_with_a_digit_is_not_an_invalid_selector(self):
        assert id_selector("2fa-code") == '[id="2fa-code"]'

    @pytest.mark.parametrize(
        "element_id",
        [
            "user.name",  # reads as #user.name
            "2fa",  # invalid identifier
            "a b",  # descendant combinator
            "field:1",  # pseudo-class
            "ctl00$Main$txt",  # ASP.NET
            "a[0]",  # attribute selector
            "a>b",  # child combinator
            "a,b",  # selector list
            "a#b",  # second id
            "-1x",  # hyphen then digit
            "",  # empty
            "emoji-🎉",  # non-ASCII
        ],
    )
    def test_awkward_ids_use_the_quoted_attribute_form(self, element_id):
        selector = id_selector(element_id)
        assert selector.startswith("[id="), f"{element_id!r} produced {selector!r}"

    @pytest.mark.parametrize("element_id", ["submit", "user_name", "user-name", "_x", "-x", "a1"])
    def test_ordinary_ids_keep_the_readable_shorthand(self, element_id):
        """
        Negative control.

        If ``id_selector`` started quoting everything, every test above would
        still pass and the output would become needlessly unreadable.
        """
        assert id_selector(element_id) == f"#{element_id}"


class TestCssStringQuoting:
    def test_plain_value(self):
        assert css_string("submit") == '"submit"'

    def test_double_quote_is_escaped(self):
        assert css_string('say "hi"') == '"say \\"hi\\""'

    def test_backslash_is_escaped(self):
        assert css_string("a\\b") == '"a\\\\b"'

    def test_single_quote_needs_no_escaping_inside_double_quotes(self):
        assert css_string("it's") == '"it\'s"'

    def test_newline_uses_the_hex_escape_the_grammar_requires(self):
        assert css_string("a\nb") == '"a\\a b"'

    def test_repr_and_css_string_disagree_where_it_matters(self):
        """
        The reason this module exists rather than keeping ``!r``.

        For a value carrying both quote characters, Python switches quoting
        style and emits a Python-specific escape. Pinning the divergence stops
        anyone concluding the two are interchangeable.
        """
        value = 'he said "x" and it\'s fine'
        assert repr(value) != css_string(value)
        assert css_string(value).startswith('"') and css_string(value).endswith('"')

    def test_empty_value(self):
        assert css_string("") == '""'


class TestAttributeAndTextSelectors:
    def test_attribute_selector_quotes_the_value(self):
        assert attribute_selector("data-testid", "sign-in") == '[data-testid="sign-in"]'

    def test_attribute_value_with_a_bracket_cannot_close_the_selector(self):
        selector = attribute_selector("aria-label", 'close] , [href')
        assert selector == '[aria-label="close] , [href"]'

    def test_text_selector_quotes_the_text(self):
        assert text_selector("button", 'Save "draft"') == 'button:has-text("Save \\"draft\\"")'


class TestPriorityOrder:
    """
    The order was stated twice — once in a module-level ``_SELECTOR_PRIORITY``
    table that nothing read, and once inline in ``_best_selector``. The dead
    table is gone; these tests are now the statement of the order.
    """

    def test_data_testid_wins(self):
        assert best(data_testid="x", aria_label="y", id="z", name="n") == '[data-testid="x"]'

    def test_aria_label_beats_placeholder(self):
        assert best(aria_label="y", placeholder="p", id="z") == '[aria-label="y"]'

    def test_placeholder_beats_id(self):
        assert best(placeholder="p", id="z") == '[placeholder="p"]'

    def test_id_beats_name(self):
        assert best(id="z", name="n") == "#z"

    def test_name_beats_text(self):
        assert best(tag="button", text="Click", name="n") == '[name="n"]'

    def test_text_is_used_for_buttons_and_links(self):
        assert best(tag="button", text="Click me") == 'button:has-text("Click me")'
        assert best(tag="a", text="Home") == 'a:has-text("Home")'

    def test_text_is_not_used_for_other_tags(self):
        assert best(tag="input", text="Click me") == "input"

    def test_role_is_the_last_resort(self):
        assert best(tag="div", role="button") == '[role="button"]'

    def test_falls_back_to_the_bare_tag(self):
        assert best(tag="input") == "input"


class TestFrameworkGeneratedIdsAreSkipped:
    @pytest.mark.parametrize("prefix", ["ember", "mat-", "ng-", "cdk-", ":"])
    def test_unstable_ids_fall_through_to_the_next_strategy(self, prefix):
        """These change between renders, so an id selector built on one rots."""
        assert best(id=f"{prefix}123", name="stable") == '[name="stable"]'

    def test_with_nothing_else_available_it_falls_past_the_id_entirely(self):
        assert best(tag="input", id="mat-input-7") == "input"


class TestIdEscapingReachesTheParser:
    def test_a_dotted_id_reaches_best_selector_correctly(self):
        """
        The unit tests above cover ``id_selector``; this proves the parser
        actually calls it rather than still formatting the id itself.
        """
        assert best(tag="input", id="user.name") == '[id="user.name"]'

    def test_a_quoted_placeholder_reaches_the_quoting(self):
        assert best(tag="input", placeholder='Enter "name"') == '[placeholder="Enter \\"name\\""]'
