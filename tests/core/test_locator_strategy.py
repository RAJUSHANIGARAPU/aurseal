"""The selector priority model: ranking, rendering, shape checks and the registry lint."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from core.locator_strategy import (
    LocatorStrategy,
    better_available,
    css_equivalent,
    lint_registry,
    priority_prompt,
    rank,
    ranked,
    render,
    shape_error,
)

ROOT = Path(__file__).resolve().parents[2]


class _Root:
    """Records which Playwright call a render made."""

    def __getattr__(self, name):
        def call(*args, **kwargs):
            return (name, args, kwargs)

        return call


class TestPriority:
    def test_order_is_most_to_least_durable(self):
        assert [s.value for s in LocatorStrategy] == [
            "data",
            "testid",
            "aria_label",
            "label",
            "role",
            "placeholder",
            "text",
            "css",
        ]
        assert rank("data") == 0 and rank({"type": "css", "value": "x"}) == 7

    def test_ranked_sorts_entries_and_keeps_ties_in_order(self):
        entries = [
            {"type": "css", "value": "a"},
            {"type": "role", "role": "button"},
            {"type": "testid", "value": "t1"},
            {"type": "testid", "value": "t2"},
        ]
        assert [e.get("value", e.get("role")) for e in ranked(entries)] == [
            "t1",
            "t2",
            "button",
            "a",
        ]

    def test_prompt_lists_the_same_order(self):
        assert priority_prompt().startswith("data-* test attribute > data-testid > aria-label")
        assert priority_prompt().endswith("text > css")

    def test_unknown_type(self):
        with pytest.raises(ValueError, match="Unsupported locator type: 'xpath'"):
            rank("xpath")


class TestRender:
    @pytest.mark.parametrize(
        ("entry", "expected"),
        [
            (
                {"type": "data", "attribute": "data-key", "value": "save"},
                ("locator", ('[data-key="save"]',), {}),
            ),
            (
                {"type": "aria_label", "value": 'Say "hi"'},
                ("locator", ('[aria-label="Say \\"hi\\""]',), {}),
            ),
            ({"type": "css", "value": "form button"}, ("locator", ("form button",), {})),
            ({"type": "testid", "value": "x"}, ("get_by_test_id", ("x",), {})),
            ({"type": "label", "value": "Email"}, ("get_by_label", ("Email",), {"exact": True})),
            ({"type": "placeholder", "value": "Search"}, ("get_by_placeholder", ("Search",), {})),
            (
                {"type": "text", "value": "Go", "exact": False},
                ("get_by_text", ("Go",), {"exact": False}),
            ),
            (
                {"type": "role", "role": "heading", "level": 1},
                ("get_by_role", ("heading",), {"level": 1}),
            ),
        ],
    )
    def test_each_strategy(self, entry, expected):
        assert render(_Root(), entry) == expected

    def test_regex_names(self):
        name, args, kwargs = render(
            _Root(), {"type": "role", "role": "button", "name": "^save$", "regex": True}
        )
        assert isinstance(kwargs["name"], re.Pattern) and kwargs["name"].flags & re.IGNORECASE

    def test_unrenderable_entry_raises(self):
        with pytest.raises(ValueError, match="no usable 'value'"):
            render(_Root(), {"type": "css"})

    def test_css_equivalent(self):
        assert css_equivalent({"type": "testid", "value": "a"}) == '[data-testid="a"]'
        assert css_equivalent({"type": "role", "role": "button"}) is None


class TestShape:
    @pytest.mark.parametrize(
        ("entry", "message"),
        [
            ("[data-testid=x]", "expected a locator mapping"),
            ({"value": "x"}, "no 'type'"),
            ({"type": "quantum", "value": "x"}, "unsupported locator type"),
            ({"type": "role"}, "no 'role'"),
            ({"type": "data", "value": "x"}, "'attribute' like 'data-key'"),
            ({"type": "data", "attribute": "class", "value": "x"}, "'attribute' like"),
            ({"type": "testid", "value": " "}, "no usable 'value'"),
        ],
    )
    def test_errors(self, entry, message):
        assert message in shape_error(entry)

    def test_good_entries(self):
        assert shape_error({"type": "role", "role": "button"}) == ""
        assert shape_error({"type": "data", "attribute": "data-key", "value": "x"}) == ""


class TestLint:
    def test_css_with_a_test_id_available_is_flagged(self):
        registry = {
            "save": {
                "type": "css",
                "value": "form button.primary",
                "attributes": {"data-testid": "save-order"},
            },
            "name": {"type": "role", "role": "textbox", "attributes": {"data-key": "name"}},
            "fine": {"type": "testid", "value": "x", "attributes": {"aria-label": "X"}},
            "broken": {"type": "css"},
        }
        assert lint_registry(registry) == [
            "save: css (rank 7) while testid (rank 1) is available",
            "name: role (rank 4) while data (rank 0) is available",
            "broken: css locator has no usable 'value'",
        ]

    def test_blank_metadata_offers_nothing(self):
        assert (
            better_available({"type": "css", "value": "a", "attributes": {"aria-label": ""}})
            is None
        )

    @pytest.mark.parametrize(
        "path",
        sorted(str(p.relative_to(ROOT)) for p in ROOT.glob("ui/**/locators/*.json")),
    )
    def test_committed_registries_are_clean(self, path):
        assert lint_registry(json.loads((ROOT / path).read_text())) == []
