"""
UI components driven against small fakes of Playwright's Page and Locator.

The fakes model only the behaviour each component depends on — and, where a
component exists because of a browser quirk, the quirk itself (a select-all that
does not select, an option click that does not take), so the guard is shown to
bite rather than assumed to.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from ui.components import dropdown, field_reader, reference_picker, wizard
from ui.components.date_entry import type_date
from ui.components.response_capture import ResponseCapture
from ui.components.universal_login import LoginError, UniversalLoginPage
from ui.components.value_entry import ValueNotLandedError, fill_amount_and_verify, fill_and_verify
from ui.fixtures.session_fixtures import storage_state_for, tagged_context_args

ROOT = Path(__file__).resolve().parents[2]


class _Keyboard:
    def __init__(self) -> None:
        self.pressed: list[str] = []

    def press(self, key: str) -> None:
        self.pressed.append(key)


class _Field:
    """A text input. ``select_all_works=False`` reproduces the macOS Control+A caret bug."""

    def __init__(self, value: str = "", select_all_works: bool = True, reformat=None) -> None:
        self.value = value
        self.selected = False
        self.select_all_works = select_all_works
        self.reformat = reformat
        self.page = SimpleNamespace(keyboard=_Keyboard())

    def scroll_into_view_if_needed(self) -> None:
        pass

    def focus(self) -> None:
        pass

    def press(self, key: str) -> None:
        if key == "ControlOrMeta+A":
            self.selected = self.select_all_works
        elif key == "Tab" and self.reformat:
            self.value = self.reformat(self.value)

    def press_sequentially(self, text: str, delay: int = 0) -> None:
        self.value = text if self.selected else text + self.value  # caret at line start
        self.selected = False

    def input_value(self) -> str:
        return self.value


class TestValueEntry:
    def test_replaces_and_verifies(self):
        field = _Field("100.00")
        assert fill_and_verify(field, "57.25") == "57.25"

    def test_a_clear_that_did_not_take_stops_the_test_at_the_cause(self):
        field = _Field("100.00", select_all_works=False)
        with pytest.raises(ValueNotLandedError, match="57.25100.00"):
            fill_and_verify(field, "57.25", description="gross amount")

    def test_amounts_compare_numerically_after_reformatting(self):
        field = _Field("", reformat=lambda v: v.replace(".", ","))
        assert fill_amount_and_verify(field, "57.25") == "57,25"


class TestDateEntry:
    def test_types_the_locale_format_and_dismisses_the_calendar(self):
        field = _Field("01-01-2020")
        assert type_date(field, date(2026, 3, 9), "nl_NL") == "09-03-2026"
        assert field.page.keyboard.pressed == ["Escape"]

    def test_a_rejected_date_fails(self):
        field = _Field("", reformat=lambda v: "")
        with pytest.raises(ValueNotLandedError, match="03/09/2026"):
            type_date(field, date(2026, 3, 9), "en_US")


class _Locator:
    """A locator over a list of items; ``first``/``last``/``filter`` narrow it."""

    def __init__(self, items, on_click=None) -> None:
        self.items = list(items)
        self._on_click = on_click

    def count(self) -> int:
        return len(self.items)

    @property
    def first(self):
        return _Locator(self.items[:1], self._on_click)

    @property
    def last(self):
        return _Locator(self.items[-1:], self._on_click)

    def filter(self, has_text=None, visible=None):
        items = self.items
        if visible is not None:
            items = [i for i in items if i.get("visible", True) == visible]
        if has_text is not None:
            items = [i for i in items if _matches(has_text, i.get("text", ""))]
        return _Locator(items, self._on_click)

    def wait_for(self, timeout=None) -> None:
        if not self.items:
            raise TimeoutError(f"Timeout {timeout}ms exceeded")

    def click(self, timeout=None) -> None:
        if not self.items or not self.items[0].get("clickable", True):
            raise TimeoutError("element is not visible")
        if self._on_click:
            self._on_click(self.items[0])

    def all_inner_texts(self):
        return [i.get("text", "") for i in self.items]


def _matches(needle, text: str) -> bool:
    return bool(needle.search(text)) if isinstance(needle, re.Pattern) else needle in text


class _Trigger:
    def __init__(self, shown: str = "") -> None:
        self.shown = shown
        self.opened = 0

    def dispatch_event(self, event: str) -> None:
        self.opened += 1

    def input_value(self) -> str:
        raise RuntimeError("not an input")

    def inner_text(self) -> str:
        return self.shown


class _DropdownPage:
    def __init__(
        self, comboboxes: int, options: list[str], trigger: _Trigger, takes_on_attempt: int = 1
    ):
        self.comboboxes = comboboxes
        self.options = options
        self.trigger = trigger
        self.takes_on_attempt = takes_on_attempt
        self.keyboard = _Keyboard()
        self.clicks = 0

    def get_by_role(self, role, name=None, exact=None):
        if role == "combobox":
            return _Locator([{}] * self.comboboxes)

        def on_click(item):
            self.clicks += 1
            if self.clicks >= self.takes_on_attempt:
                self.trigger.shown = item["text"]

        items = [{"text": o} for o in self.options if name is None or o == name]
        return _Locator(items, on_click)

    def wait_for_load_state(self, state, timeout=None) -> None:
        pass


class TestDropdown:
    def test_selects_and_reads_back(self):
        trigger = _Trigger()
        page = _DropdownPage(1, ["Monthly", "Yearly"], trigger)
        assert dropdown.select_option(page, trigger, "Yearly") == "Yearly"

    def test_a_click_that_did_not_take_is_retried_after_escape(self):
        trigger = _Trigger()
        page = _DropdownPage(1, ["Monthly", "Yearly"], trigger, takes_on_attempt=2)
        assert dropdown.select_option(page, trigger, "Yearly") == "Yearly"
        assert trigger.opened == 2 and page.keyboard.pressed == ["Escape"]

    def test_gives_up_naming_the_options_it_saw(self):
        trigger = _Trigger()
        page = _DropdownPage(1, ["Monthly"], trigger)
        with pytest.raises(dropdown.DropdownError, match=r"Options seen: \['Monthly'\]"):
            dropdown.select_option(page, trigger, "Weekly", attempts=2, option_timeout_ms=1)

    @pytest.mark.parametrize("count", [0, 2])
    def test_the_field_must_be_exactly_one_dropdown(self, count):
        page = _DropdownPage(count, [], _Trigger())
        with pytest.raises(dropdown.DropdownError, match=f"found {count}"):
            dropdown.combobox_by_label(page, "Term")


class _WizardPage:
    def __init__(self, buttons, idle=True):
        self.buttons = buttons
        self.idle = idle
        self.url = "https://app.example.test/flow"
        self.clicked: list[str] = []

    def wait_for_load_state(self, state, timeout=None):
        if not self.idle:
            raise TimeoutError("networkidle")

    def get_by_role(self, role, name=None, exact=None):
        if role == "alert":
            return _Locator([{"text": "Email is required", "visible": True}])
        items = [b for b in self.buttons if name is None or b["text"] == name]
        return _Locator(items, on_click=lambda item: self.clicked.append(item["id"]))


class TestWizard:
    def test_the_visible_copy_is_clicked_not_the_first(self):
        page = _WizardPage(
            [
                {"id": "old", "text": "Submit details", "visible": False},
                {"id": "current", "text": "Submit details", "visible": True},
            ]
        )
        wizard.submit_step(page, "Submit details")
        assert page.clicked == ["current"]

    def test_settle_does_not_throw(self):
        assert wizard.settle(_WizardPage([], idle=False), 10) is False
        assert wizard.settle(_WizardPage([]), 10) is True

    def test_an_unclickable_submit_logs_what_is_on_screen_and_raises(self):
        from tests.core._log_capture import framework_log

        page = _WizardPage([{"id": "x", "text": "Submit details", "clickable": False}])
        with framework_log() as log, pytest.raises(TimeoutError):
            wizard.submit_step(page, "Submit details")
        assert "Email is required" in log.text

    def test_alerts(self):
        assert wizard.has_visible_alerts(_WizardPage([]))


class _Element:
    def __init__(self, test_id="", label="", value=None, text="", readonly=False, disabled=False):
        self.test_id, self.label, self.value, self.text = test_id, label, value, text
        self.readonly, self.disabled = readonly, disabled

    def input_value(self):
        if self.value is None:
            raise RuntimeError("not an input")
        return self.value

    def text_content(self):
        return self.text

    def get_attribute(self, name):
        return "" if name == "readonly" and self.readonly else None

    def is_disabled(self):
        return self.disabled

    def is_editable(self):
        return self.value is not None and not self.readonly and not self.disabled


class _Elements:
    def __init__(self, items):
        self.items = items

    def count(self):
        return len(self.items)

    def __getattr__(self, name):
        return getattr(self.items[0], name)


class _FormPage:
    def __init__(self, elements):
        self.elements = elements

    def get_by_test_id(self, pattern):
        return _Elements([e for e in self.elements if e.test_id and pattern.search(e.test_id)])

    def get_by_label(self, label, exact=True):
        return _Elements([e for e in self.elements if e.label == label])


class TestFieldReader:
    def test_the_error_twin_cannot_answer_for_the_field(self):
        page = _FormPage(
            [
                _Element(test_id="form.customer.email-error", text="Email has an invalid value"),
                _Element(test_id="form.customer.email", value="ada@example.test"),
            ]
        )
        result = field_reader.read_field(page, path="customer.email", label="Email")
        assert (result.raw, result.via) == ("ada@example.test", "test-id")

    def test_label_fallback_strips_the_echo_of_a_static_field(self):
        page = _FormPage([_Element(label="Company type", text="Company type *")])
        result = field_reader.read_field(page, path="company.type", label="Company type")
        assert result.via == "label" and result.raw == "" and result.empty()

    def test_an_ambiguous_label_is_refused(self):
        page = _FormPage([_Element(label="Term", value="1"), _Element(label="Term", value="2")])
        with pytest.raises(field_reader.AmbiguousFieldError):
            field_reader.read_field(page, label="Term")

    def test_absent_is_reported_as_absent(self):
        result = field_reader.read_field(_FormPage([]), label="Missing")
        assert result.via == "absent" and result.empty()

    def test_read_only_detection(self):
        page = _FormPage(
            [
                _Element(test_id="a", value="1", readonly=True),
                _Element(test_id="b", value="1", disabled=True),
                _Element(test_id="c", text="static"),
                _Element(test_id="d", value="1"),
            ]
        )
        assert field_reader.field_is_read_only(page, path="a")
        assert field_reader.field_is_read_only(page, path="b")
        assert field_reader.field_is_read_only(page, path="c")
        assert field_reader.field_is_read_only(page, path="d") is False
        assert field_reader.field_is_read_only(page, path="zzz") is None

    def test_static_text_whose_editability_cannot_be_asked_is_read_only(self):
        class _Static(_Element):
            def is_editable(self):
                raise RuntimeError("Element is not an <input>, <textarea> or <select>")

        page = _FormPage([_Static(test_id="shown", text="Jordan"), _Element(test_id="x", value="")])
        assert field_reader.field_is_read_only(page, path="shown") is True

    def test_needs_a_path_or_label(self):
        with pytest.raises(ValueError):
            field_reader.read_field(_FormPage([]))


class _PickerPage:
    """One dialog; rows appear after a search when ``found_by_search``."""

    def __init__(self, default_rows, search_rows=()):
        self.rows = [{"text": t} for t in default_rows]
        self.search_rows = [{"text": t} for t in search_rows]
        self.dialog_open = True
        self.searched: list[str] = []

    def wait_for_load_state(self, state, timeout=None):
        pass

    def get_by_role(self, role, **kwargs):
        assert role == "dialog"
        page = self

        class Dialog(_Locator):
            def get_by_role(self, inner_role, **kw):
                if inner_role == "row":
                    return _Locator(
                        page.rows, on_click=lambda item: setattr(page, "dialog_open", False)
                    )
                box = SimpleNamespace(
                    fill=lambda term: page.searched.append(term),
                    press=lambda key: setattr(page, "rows", page.search_rows),
                )
                found = SimpleNamespace(count=lambda: 1, first=box)
                missing = SimpleNamespace(count=lambda: 0, first=None)
                return found if inner_role == "searchbox" else missing

            @property
            def last(self):
                return Dialog(self.items[-1:])

        return Dialog([{}] if self.dialog_open else [])


class TestReferencePicker:
    @pytest.fixture(autouse=True)
    def _no_expect(self, monkeypatch):
        monkeypatch.setattr(
            reference_picker,
            "expect",
            lambda locator: SimpleNamespace(to_have_count=lambda n, timeout: None),
        )

    def test_the_default_list_is_tried_first(self):
        page = _PickerPage(["Acme Ltd", "Globex BV"])
        assert reference_picker.pick(page, "Globex") == "default-list"
        assert page.searched == [] and not page.dialog_open

    def test_search_when_not_in_the_default_list(self):
        page = _PickerPage(["Acme Ltd"], search_rows=["Initech 4711"])
        assert reference_picker.pick(page, "Initech", default_list_timeout_ms=1) == "search"
        assert page.searched == ["Initech"]

    def test_not_found_lists_the_rows_it_saw(self):
        page = _PickerPage(["Acme Ltd"], search_rows=["Hooli"])
        with pytest.raises(reference_picker.ReferencePickError, match="Hooli"):
            reference_picker.pick(page, "Initech", default_list_timeout_ms=1, row_timeout_ms=1)


class _CapturePage:
    def __init__(self):
        self.listeners: list = []

    def on(self, event, handler):
        self.listeners.append(handler)

    def remove_listener(self, event, handler):
        self.listeners.remove(handler)


def _resp(url, status=200, body=b"{}"):
    return SimpleNamespace(url=url, status=status, body=lambda: body)


class TestResponseCapture:
    def test_keeps_the_latest_successful_json_of_the_named_call(self):
        page = _CapturePage()
        with ResponseCapture(page, "/quote") as capture:
            capture.on_response(_resp("https://app.example.test/api/quote", body=b'{"total": 10}'))
            capture.on_response(_resp("https://app.example.test/api/quote", 500, b'{"total": 99}'))
            capture.on_response(_resp("https://app.example.test/api/other", body=b'{"total": 77}'))
            capture.on_response(_resp("https://app.example.test/api/quote", body=b"<html>"))
            capture.on_response(
                _resp("https://app.example.test/api/quote?v=2", body=b'{"total": 12.345}')
            )
        assert page.listeners == []
        assert capture.count == 2
        assert str(capture.amount_at("total")) == "12.35"
        assert capture.latest_url.endswith("v=2")

    def test_regex_and_predicate_matchers(self):
        assert ResponseCapture(_CapturePage(), re.compile(r"/quote$"))._matches(
            "https://x.example.test/quote"
        )
        assert ResponseCapture(_CapturePage(), lambda url: "q" in url)._matches("q")

    def test_nothing_captured(self):
        capture = ResponseCapture(_CapturePage(), "/quote")
        assert capture.amount_at("total") is None and capture.first_amount(["total"]) is None


class _LoginLocator:
    def __init__(self, page, key):
        self.page, self.key = page, key

    def filter(self, visible=None):
        return self

    @property
    def first(self):
        return self

    def count(self):
        return 1 if self.key in self.page.visible else 0

    def wait_for(self, timeout=None):
        if self.key not in self.page.visible:
            raise TimeoutError(self.key)

    def fill(self, value):
        self.page.filled[self.key] = value

    def click(self, timeout=None):
        self.page.click(self.key)


class _LoginPage:
    def __init__(self, identifier_first: bool, orgs=()):
        self.identifier_first = identifier_first
        self.visible = {"username", "submit"} | (set() if identifier_first else {"password"})
        self.orgs = set(orgs)
        self.filled: dict = {}
        self.clicks: list[str] = []
        self.url = "https://login.example.test/u/login"
        self.waited_for = None
        self.goto_failures = 0

    def goto(self, url):
        if self.goto_failures:
            self.goto_failures -= 1
            raise RuntimeError("net::ERR_NAME_NOT_RESOLVED\nmore detail")

    def wait_for_timeout(self, ms):
        pass

    def click(self, key):
        self.clicks.append(key)
        if key == "submit" and "password" not in self.visible:
            self.visible.add("password")

    def get_by_label(self, pattern, exact=True):
        return _LoginLocator(self, "password" if "password" in pattern.pattern else "username")

    def get_by_role(self, role, name=None, exact=None):
        if role == "button" and isinstance(name, re.Pattern):
            return _LoginLocator(self, "submit")
        page = self

        class Org(_LoginLocator):
            def count(self):
                return 1 if name in page.orgs and role == "button" else 0

            def click(self, timeout=None):
                if name not in page.orgs:
                    raise TimeoutError("no picker")
                page.clicks.append(f"org:{name}")

        return Org(self, name)

    def wait_for_url(self, target, timeout=None):
        self.waited_for = target


class TestUniversalLogin:
    @pytest.mark.parametrize("identifier_first, submits", [(True, 2), (False, 1)])
    def test_both_login_shapes(self, identifier_first, submits):
        page = _LoginPage(identifier_first)
        UniversalLoginPage(page).login(
            "https://app.example.test/",
            "tester@example.test",
            "pw",
            app_url="https://app.example.test/",
        )
        assert page.filled == {"username": "tester@example.test", "password": "pw"}
        assert page.clicks.count("submit") == submits
        assert page.waited_for.pattern == "^" + re.escape("https://app.example.test")

    def test_organisation_picker(self):
        page = _LoginPage(False, orgs=["Example Org"])
        login = UniversalLoginPage(page)
        assert login.choose_organization("Example Org")
        assert not login.choose_organization("Other Org", timeout_ms=1)

    def test_navigation_is_retried_then_reported(self):
        page = _LoginPage(False)
        page.goto_failures = 1
        UniversalLoginPage(page).navigate_with_retry("https://app.example.test/", attempts=2)
        page.goto_failures = 5
        with pytest.raises(LoginError, match="after 2 attempts"):
            UniversalLoginPage(page).navigate_with_retry("https://app.example.test/", attempts=2)

    def test_missing_credentials_fail_before_the_browser_does_anything(self):
        with pytest.raises(LoginError, match="AUTH_PASSWORD"):
            UniversalLoginPage(_LoginPage(False)).login("https://app.example.test/", "u", "")

    def test_locators_are_json_managed(self):
        locators = json.loads((ROOT / "ui/components/locators/universal_login.json").read_text())
        assert {v["type"] for v in locators.values()} <= {"label", "role", "testid", "text"}


class TestSessionFixtures:
    def test_non_ui_modes_need_no_login(self):
        from core.auth.settings import AuthMode, AuthSettings

        settings = AuthSettings(AuthMode.NONE, "u", "u")
        assert storage_state_for(None, settings, None, None) is None

    def test_ui_mode_without_credentials_skips_with_the_variable_names(self):
        from core.auth.settings import AuthMode, AuthSettings

        with pytest.raises(pytest.skip.Exception, match="AUTH_USERNAME, AUTH_PASSWORD"):
            storage_state_for(None, AuthSettings(AuthMode.UI, "u", "u"), None, None)

    def test_context_args_are_tagged_and_carry_the_login(self, monkeypatch):
        monkeypatch.setenv("TEST_RUN_ID", "r5")
        monkeypatch.delenv("TEST_RUN_TAGGING", raising=False)
        args = tagged_context_args(
            {"user_agent": "Mozilla/5.0 X", "locale": "en-US"}, None, Path("/tmp/s.json")
        )
        assert args["user_agent"] == "Mozilla/5.0 X test-automation/r5"
        assert args["storage_state"] == "/tmp/s.json" and args["locale"] == "en-US"
