"""
The demo page objects' offline parts: the locator registry, templates, the resend
rule, native select/date helpers and the pure readers. No browser is started.
"""

from __future__ import annotations

import json
import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.locator_strategy import shape_error
from tests.ui.test_submit_outcome import _Page, _response
from ui.components import dropdown
from ui.components.date_entry import set_native_date
from ui.components.resend_rule import (
    TrackedSubmit,
    next_choice,
    server_did_not_act,
    submit_tracking_sent,
)
from ui.components.submit_outcome import SubmitResult
from ui.components.value_entry import ValueNotLandedError
from ui.pages.demo.base import LOCATOR_FILE, fill_template, template_fields
from ui.pages.demo.subscription_detail import ledger_row
from ui.pages.demo.subscription_wizard import AddressBlock, exact_number

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = json.loads(LOCATOR_FILE.read_text())
DEMO_SOURCE = (ROOT / "demo_sut" / "ui.py").read_text()
#: test ids the demo composes at render time (``f"pick-{kind}"``), keyed by their template
COMPOSED = {
    template.format(kind=kind): template
    for template in ("pick-{kind}", "{kind}-summary", "{kind}-change")
    for kind in ("partner", "customer")
}


# -- locator registry -------------------------------------------------------------------


@pytest.mark.parametrize("key", sorted(REGISTRY))
def test_every_entry_renders_and_names_something_the_demo_ships(key):
    entry = fill_template(REGISTRY[key], dict.fromkeys(template_fields(REGISTRY[key]), "x"))
    assert not shape_error(entry), (key, shape_error(entry))
    raw = REGISTRY[key]
    if raw["type"] in ("testid", "data"):
        fixed = re.split(r"\{\w+\}", raw["value"])[0]
        if fixed in COMPOSED:
            assert COMPOSED[fixed] in DEMO_SOURCE, f"{key}: template {COMPOSED[fixed]!r} gone"
        elif fixed:
            assert fixed in DEMO_SOURCE, f"{key}: {raw['value']!r} not found in demo_sut/ui.py"


def test_registry_avoids_raw_css():
    assert [k for k, v in REGISTRY.items() if v["type"] == "css"] == []


def test_templates_fill_every_placeholder_and_refuse_a_missing_one():
    entry = {"type": "testid", "value": "address-{n}-{field}"}
    assert template_fields(entry) == {"n", "field"}
    assert fill_template(entry, {"n": 2, "field": "postcode"})["value"] == "address-2-postcode"
    with pytest.raises(KeyError, match="field"):
        fill_template(entry, {"n": 1})


# -- resend rule ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sent", "status", "safe"),
    [
        (False, None, True),
        (True, 400, True),
        (True, 422, True),
        (True, 499, True),
        (True, 200, False),
        (True, 201, False),
        (True, 500, False),
        (True, 504, False),
        (True, None, False),
    ],
)
def test_only_an_unsent_or_refused_write_may_be_resent(sent, status, safe):
    assert server_did_not_act(sent, status) is safe
    assert TrackedSubmit(sent, SubmitResult(status=status)).may_resend is safe


def test_next_choice_follows_preference_and_skips_unoffered_and_tried():
    preferred = ["GONE", "OTHER", "MOVED"]
    offered = ["MOVED", "OTHER", "BUDGET"]
    assert next_choice(preferred, offered, []) == "OTHER"
    assert next_choice(preferred, offered, ["OTHER"]) == "MOVED"
    assert next_choice(preferred, offered, ["OTHER", "MOVED"]) is None


# -- native select and date ---------------------------------------------------------------


class _Select:
    def __init__(self, options, sticky=False):
        self.options = options  # [(value, label)]
        self.index = 0
        self.sticky = sticky  # True: the choice never lands

    def select_option(self, label=None, value=None):
        for i, (v, text) in enumerate(self.options):
            if text == label or v == value:
                if not self.sticky:
                    self.index = i
                return
        raise RuntimeError("no such option")

    def evaluate(self, script):
        return self.options[self.index][1]

    def input_value(self):
        return self.options[self.index][0]

    def get_by_role(self, role):
        labels = [text for _, text in self.options]

        class _Options:
            def all_inner_texts(self):
                return labels

        return _Options()


def test_native_select_reads_the_choice_back():
    select = _Select([("M", "Monthly"), ("Y", "Yearly")])
    assert dropdown.select_native(select, "Yearly") == "Yearly"
    assert dropdown.select_native(select, "M", by="value") == "Monthly"


def test_native_select_that_does_not_take_names_the_options():
    select = _Select([("M", "Monthly"), ("Y", "Yearly")], sticky=True)
    with pytest.raises(dropdown.DropdownError, match="Options seen: \\['Monthly', 'Yearly'\\]"):
        dropdown.select_native(select, "Yearly", attempts=2)
    with pytest.raises(ValueError):
        dropdown.select_native(select, "Yearly", by="index")


class _DateInput:
    def __init__(self, accepts=True):
        self.value, self.accepts = "", accepts

    def fill(self, text):
        self.value = text if self.accepts else ""

    def input_value(self):
        return self.value


def test_native_date_is_set_as_iso_and_verified():
    assert set_native_date(_DateInput(), date(2026, 4, 15)) == "2026-04-15"
    with pytest.raises(ValueNotLandedError):
        set_native_date(_DateInput(accepts=False), date(2026, 4, 15))


# -- pure readers -------------------------------------------------------------------------


def test_reference_numbers_match_whole():
    pattern = exact_number("CUS-D1-1")
    assert pattern.search("CUS-D1-1Test Person")
    assert not pattern.search("CUS-D1-12Test Person")


def test_address_block_placeholder_reads_as_empty():
    assert AddressBlock("XA", "12 Harbour Row, QX 4410, Lowmarsh, XA").filled
    assert not AddressBlock("", "12 Harbour Row").filled
    assert not AddressBlock("XA", "-").filled


def test_ledger_row_types_amount_columns_only():
    row = ledger_row({"kind": "NEW", "net": "40.00", "total": "-1,234.50", "state": " BOOKED "})
    assert row == {
        "kind": "NEW",
        "net": Decimal("40.00"),
        "total": Decimal("-1234.50"),
        "state": "BOOKED",
    }


class _TrackingPage(_Page):
    """The submit fake, plus request events: ``sends`` decides whether a click sends."""

    def __init__(self, response, sends=True):
        super().__init__(response)
        self.sends = sends
        self.listeners = []

    def on(self, event, handler):
        assert event == "request"
        self.listeners.append(handler)

    def remove_listener(self, event, handler):
        self.listeners.remove(handler)

    def click(self):
        if self.sends:
            request = SimpleNamespace(method="POST", url=self.response.url)
            for handler in list(self.listeners):
                handler(request)


@pytest.mark.parametrize(
    ("sends", "status", "may_resend"),
    [(True, 422, True), (True, 504, False), (False, 422, True)],
)
def test_tracked_submit_knows_whether_the_call_left(sends, status, may_resend):
    page = _TrackingPage(_response(status), sends=sends)
    tracked = submit_tracking_sent(page, page.click, "/submit", timeout_ms=10, settle_ms=0)
    assert tracked.sent is sends
    assert tracked.may_resend is may_resend
    assert page.listeners == [], "the request listener must be removed"
