"""
One small object for a screen whose every hook is a test id.

Admin and back-office screens ship one at a time and are checked one at a time:
a settings form, a ledger table, a message list with a per-row menu. Writing a
full page object per screen costs more than the check is worth, so ``Screen``
offers the handful of operations such a check needs, keyed on the app's test-id
attribute (``data-testid`` by default; any ``data-*`` attribute the front end
uses) or an ARIA role — never on a CSS class.

**Opening fails fast on a login redirect.** A screen opened without a valid
session lands on the login page, and the "ready" element then never appears, so
the check times out thirty seconds later with a message about a missing element.
``open`` watches for both outcomes and raises ``LoginRedirectError`` naming the
URL it landed on as soon as the login page shows.

Reading returns plain values (``EntryState``, ``WidgetState``, counts, texts) so
an assertion message can show what was on screen. Acting is ``type_into``,
``select_option``, ``choose_menu_option``, ``open_tab``, ``click`` and
``press_escape``.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import urljoin

from core.locator_strategy import render
from core.logger import logger

DEFAULT_LOGIN_MARKERS = ("/login", "/signin", "/authorize")


class LoginRedirectError(AssertionError):
    """The screen redirected to a login page: the session is missing or expired."""


class ScreenNotReadyError(AssertionError):
    """The ready element did not appear in time."""


@dataclass(frozen=True)
class EntryState:
    value: str
    visible: bool
    enabled: bool
    editable: bool
    required: bool
    invalid: bool

    @property
    def read_only(self) -> bool:
        return self.visible and not self.editable


@dataclass(frozen=True)
class WidgetState:
    text: str
    visible: bool
    enabled: bool
    checked: bool | None  # aria-checked / aria-selected / aria-pressed, None when absent


def _attr_true(locator, name: str) -> bool:
    value = locator.get_attribute(name)
    return value is not None and value.lower() != "false"


class Screen:
    def __init__(
        self,
        page,
        base_url: str = "",
        *,
        test_id_attribute: str = "data-testid",
        login_markers: Iterable[str] = DEFAULT_LOGIN_MARKERS,
        row_selector: str = "tbody tr",
    ) -> None:
        self.page = page
        self.base_url = base_url
        self.test_id_attribute = test_id_attribute
        self.login_markers = tuple(login_markers)
        self.row_selector = row_selector

    # ── locating ─────────────────────────────────────────────────────────────

    def by_test_id(self, test_id: str, root=None):
        """The element(s) carrying ``test_id`` under ``root`` (default: the page)."""
        entry = (
            {"type": "testid", "value": test_id}
            if self.test_id_attribute == "data-testid"
            else {"type": "data", "attribute": self.test_id_attribute, "value": test_id}
        )
        return render(root if root is not None else self.page, entry)

    # ── opening ──────────────────────────────────────────────────────────────

    def on_login_page(self) -> bool:
        return any(marker in self.page.url for marker in self.login_markers)

    def open(self, route: str, ready_test_id: str, *, timeout_ms: int = 15000) -> Screen:
        """
        Navigate to ``route`` and wait until ``ready_test_id`` is visible.

        Raises ``LoginRedirectError`` as soon as a login page shows, and
        ``ScreenNotReadyError`` when neither happens within ``timeout_ms``.
        """
        url = urljoin(self.base_url, route) if self.base_url else route
        self.page.goto(url)
        ready = self.by_test_id(ready_test_id).first
        deadline = time.monotonic() + timeout_ms / 1000
        while True:
            if self.on_login_page():
                raise LoginRedirectError(
                    f"opening {route} landed on a login page ({self.page.url}); "
                    "the session is missing or expired"
                )
            if ready.count() > 0 and ready.is_visible():
                return self
            if time.monotonic() >= deadline:
                raise ScreenNotReadyError(
                    f"{route}: [{self.test_id_attribute}={ready_test_id}] not visible "
                    f"within {timeout_ms}ms (at {self.page.url})"
                )
            self.page.wait_for_timeout(100)

    # ── reading ──────────────────────────────────────────────────────────────

    def count(self, test_id: str) -> int:
        return self.by_test_id(test_id).count()

    def text(self, test_id: str) -> str:
        return self.by_test_id(test_id).first.inner_text().strip()

    def read_input(self, test_id: str) -> EntryState:
        """State of the input carrying ``test_id`` (or the first input inside it)."""
        host = self.by_test_id(test_id).first
        field = host
        if (host.evaluate("e => e.tagName") or "").lower() not in ("input", "textarea", "select"):
            field = host.locator("input, textarea, select").first
        return EntryState(
            value=field.input_value(),
            visible=field.is_visible(),
            enabled=field.is_enabled(),
            editable=field.is_editable(),
            required=_attr_true(field, "required") or _attr_true(field, "aria-required"),
            invalid=_attr_true(field, "aria-invalid"),
        )

    def read_control(self, test_id: str) -> WidgetState:
        control = self.by_test_id(test_id).first
        checked = None
        for name in ("aria-checked", "aria-selected", "aria-pressed"):
            value = control.get_attribute(name)
            if value is not None:
                checked = value.lower() == "true"
                break
        return WidgetState(
            text=(control.inner_text() or "").strip(),
            visible=control.is_visible(),
            enabled=control.is_enabled(),
            checked=checked,
        )

    def rows(self, table_test_id: str):
        return self.by_test_id(table_test_id).first.locator(self.row_selector)

    def row_count(self, table_test_id: str) -> int:
        return self.rows(table_test_id).count()

    def wait_for_rows(
        self, table_test_id: str, minimum: int = 1, *, timeout_ms: int = 15000
    ) -> int:
        """Poll until the table has at least ``minimum`` rows; returns the count seen."""
        deadline = time.monotonic() + timeout_ms / 1000
        while True:
            seen = self.row_count(table_test_id)
            if seen >= minimum or time.monotonic() >= deadline:
                if seen < minimum:
                    logger.warning(
                        "table %s has %d rows after %dms (wanted %d)",
                        table_test_id,
                        seen,
                        timeout_ms,
                        minimum,
                    )
                return seen
            self.page.wait_for_timeout(100)

    def _visible_menu_items(self) -> list[str]:
        items = self.page.get_by_role("menuitem").filter(visible=True)
        return [t.strip() for t in items.all_inner_texts() if t and t.strip()]

    def row_menu_items(self, table_test_id: str, row_index: int, menu_test_id: str) -> list[str]:
        """Open row ``row_index``'s menu, read its items, close it again."""
        row = self.rows(table_test_id).nth(row_index)
        self.by_test_id(menu_test_id, root=row).first.click()
        try:
            return self._visible_menu_items()
        finally:
            self.press_escape()

    def menu_options(self, trigger_test_id: str) -> list[str]:
        """Open a menu or select by its trigger, read the options, close it again."""
        self.by_test_id(trigger_test_id).first.click()
        try:
            options = self.page.get_by_role("option").filter(visible=True).all_inner_texts()
            texts = [t.strip() for t in options if t and t.strip()]
            return texts or self._visible_menu_items()
        finally:
            self.press_escape()

    # ── acting ───────────────────────────────────────────────────────────────

    def type_into(self, test_id: str, text: str) -> None:
        field = self.by_test_id(test_id).first
        field.fill("")
        field.fill(text)

    def select_option(self, trigger_test_id: str, option: str | re.Pattern[str]) -> None:
        self.by_test_id(trigger_test_id).first.click()
        self.page.get_by_role("option", name=option).filter(visible=True).first.click()

    def choose_menu_option(self, trigger_test_id: str, item: str | re.Pattern[str]) -> None:
        self.by_test_id(trigger_test_id).first.click()
        self.page.get_by_role("menuitem", name=item).filter(visible=True).first.click()

    def open_tab(self, name: str | re.Pattern[str]) -> None:
        self.page.get_by_role("tab", name=name).first.click()

    def click(self, test_id: str) -> None:
        self.by_test_id(test_id).first.click()

    def press_escape(self) -> None:
        self.page.keyboard.press("Escape")
