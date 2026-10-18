"""The test-id driven screen object, against a fake page."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from ui.components.screen import (
    EntryState,
    LoginRedirectError,
    Screen,
    ScreenNotReadyError,
    WidgetState,
)


class _El:
    def __init__(
        self,
        tag="div",
        text="",
        value="",
        attrs=None,
        visible=True,
        enabled=True,
        editable=True,
        children=None,
        on_click=None,
    ):
        self.tag, self.text_, self.value = tag, text, value
        self.attrs = attrs or {}
        self.visible, self.enabled, self.editable = visible, enabled, editable
        self.children = children or {}
        self.on_click = on_click
        self.clicks = 0

    # locator surface for a single element
    def count(self):
        return 1

    def evaluate(self, script):
        return self.tag.upper()

    def input_value(self):
        return self.value

    def is_visible(self):
        return self.visible

    def is_enabled(self):
        return self.enabled

    def is_editable(self):
        return self.editable

    def get_attribute(self, name):
        return self.attrs.get(name)

    def inner_text(self):
        return self.text_

    def click(self, **kw):
        self.clicks += 1
        if self.on_click:
            self.on_click()

    def fill(self, text):
        self.value = text

    def locator(self, selector):
        return _Loc(self.children.get(selector, []))

    def get_by_test_id(self, value):
        return _Loc(self.children.get(("testid", value), []))


class _Loc:
    def __init__(self, elements):
        self.elements = list(elements)

    @property
    def first(self):
        return self.elements[0] if self.elements else _El(visible=False)

    def nth(self, i):
        return self.elements[i]

    def count(self):
        return len(self.elements)

    def filter(self, **kw):
        return _Loc([e for e in self.elements if e.visible])

    def all_inner_texts(self):
        return [e.text_ for e in self.elements]


class _Page:
    def __init__(self, url_after_goto="https://app.example.test/admin/settings"):
        self.url = ""
        self.url_after_goto = url_after_goto
        self.by_id: dict[str, list[_El]] = {}
        self.data_attr: dict[str, list[_El]] = {}
        self.roles: dict[str, list[_El]] = {}
        self.waits = 0
        self.keyboard = SimpleNamespace(pressed=[])
        self.keyboard.press = self.keyboard.pressed.append
        self.visited = []

    def goto(self, url):
        self.visited.append(url)
        self.url = self.url_after_goto

    def get_by_test_id(self, value):
        return _Loc(self.by_id.get(value, []))

    def locator(self, selector):
        return _Loc(self.data_attr.get(selector, []))

    def get_by_role(self, role, name=None, **kw):
        found = self.roles.get(role, [])
        if name is not None:
            found = [e for e in found if e.text_ == name]
        return _Loc(found)

    def wait_for_timeout(self, ms):
        self.waits += 1


class TestOpen:
    def test_opens_when_ready_element_shows(self):
        page = _Page()
        page.by_id["settings-form"] = [_El()]
        screen = Screen(page, "https://app.example.test/").open("admin/settings", "settings-form")
        assert screen is not None
        assert page.visited == ["https://app.example.test/admin/settings"]

    def test_login_redirect_fails_fast_naming_the_url(self):
        page = _Page("https://id.example.test/login?next=/admin")
        with pytest.raises(LoginRedirectError, match="id.example.test/login"):
            Screen(page).open("/admin/settings", "settings-form", timeout_ms=10_000)
        assert page.waits == 0

    def test_not_ready_in_time(self):
        page = _Page()
        page.by_id["settings-form"] = [_El(visible=False)]
        with pytest.raises(ScreenNotReadyError, match="settings-form"):
            Screen(page).open("/admin/settings", "settings-form", timeout_ms=1)

    def test_custom_test_id_attribute_uses_a_data_selector(self):
        page = _Page()
        page.data_attr['[data-ta="ledger"]'] = [_El()]
        Screen(page, test_id_attribute="data-ta").open("/admin/ledger", "ledger")


class TestReading:
    def test_read_input_on_the_input_itself(self):
        page = _Page()
        page.by_id["currency"] = [
            _El(
                "input",
                value="EUR",
                editable=False,
                attrs={"aria-invalid": "false", "required": ""},
            )
        ]
        state = Screen(page).read_input("currency")
        assert state == EntryState("EUR", True, True, False, True, False)
        assert state.read_only

    def test_read_input_inside_a_wrapper(self):
        inner = _El("input", value="42", attrs={"aria-invalid": "true", "aria-required": "true"})
        page = _Page()
        page.by_id["amount"] = [_El("div", children={"input, textarea, select": [inner]})]
        state = Screen(page).read_input("amount")
        assert state.value == "42" and state.invalid and state.required

    def test_read_control(self):
        page = _Page()
        page.by_id["auto-renew"] = [_El(text=" Auto renew ", attrs={"aria-checked": "true"})]
        page.by_id["plain"] = [_El(text="Plain", enabled=False)]
        screen = Screen(page)
        assert screen.read_control("auto-renew") == WidgetState("Auto renew", True, True, True)
        assert screen.read_control("plain").checked is None

    def test_counts_text_and_rows(self):
        rows = [_El(text=f"row {i}") for i in range(3)]
        page = _Page()
        page.by_id["ledger"] = [_El(children={"tbody tr": rows})]
        page.by_id["title"] = [_El(text="  Ledger  ")]
        screen = Screen(page)
        assert screen.count("ledger") == 1
        assert screen.text("title") == "Ledger"
        assert screen.row_count("ledger") == 3
        assert screen.wait_for_rows("ledger", 2) == 3
        assert screen.wait_for_rows("ledger", 5, timeout_ms=1) == 3

    def test_row_menu_items_opens_reads_and_closes(self):
        page = _Page()
        page.roles["menuitem"] = [
            _El(text="Resend"),
            _El(text="Download"),
            _El(text="hidden", visible=False),
        ]
        trigger = _El()
        row = _El(children={("testid", "row-menu"): [trigger]})
        page.by_id["messages"] = [_El(children={"tbody tr": [_El(), row]})]
        items = Screen(page).row_menu_items("messages", 1, "row-menu")
        assert items == ["Resend", "Download"]
        assert trigger.clicks == 1
        assert page.keyboard.pressed == ["Escape"]

    def test_menu_options_prefers_options_then_menu_items(self):
        page = _Page()
        page.by_id["currency"] = [_El()]
        page.roles["option"] = [_El(text="EUR"), _El(text="USD")]
        assert Screen(page).menu_options("currency") == ["EUR", "USD"]
        page.roles["option"] = []
        page.roles["menuitem"] = [_El(text="Export")]
        assert Screen(page).menu_options("currency") == ["Export"]


class TestActing:
    def test_type_select_choose_tab_click_escape(self):
        page = _Page()
        field = _El("input", value="old")
        trigger = _El()
        page.by_id.update({"name": [field], "currency": [trigger], "save": [_El()]})
        usd = _El(text="USD")
        export = _El(text="Export")
        tab = _El(text="History")
        page.roles.update({"option": [usd], "menuitem": [export], "tab": [tab]})
        screen = Screen(page)
        screen.type_into("name", "new")
        screen.select_option("currency", "USD")
        screen.choose_menu_option("currency", "Export")
        screen.open_tab("History")
        screen.click("save")
        screen.press_escape()
        assert field.value == "new"
        assert usd.clicks == export.clicks == tab.clicks == 1
        assert trigger.clicks == 2
        assert page.by_id["save"][0].clicks == 1
        assert page.keyboard.pressed == ["Escape"]
