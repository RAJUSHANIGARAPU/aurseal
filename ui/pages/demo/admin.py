"""The demo's admin screens: the card overview, ledger accounts, integrations, settlements."""

from __future__ import annotations

from dataclasses import dataclass

from ui.components.field_reader import field_is_read_only, read_field
from ui.components.response_capture import ResponseCapture
from ui.components.screen import EntryState
from ui.pages.demo.base import DemoPage
from ui.pages.demo.subscription_wizard import exact_number


class AdminOverview(DemoPage):
    ready = "admin.cards"

    def open(self) -> AdminOverview:
        self.goto("/ui/admin")
        return self

    def card_titles(self) -> list[str]:
        return [t.strip() for t in self.resolve("admin.card_heading").all_inner_texts()]

    def links(self) -> list[str]:
        return [t.strip() for t in self.resolve("admin.any_link").all_inner_texts()]

    def link_count(self, key: str) -> int:
        return self.resolve("admin.link", key=key).count()

    def breadcrumb(self) -> list[str]:
        crumbs = self.resolve("page.breadcrumb").first.inner_text()
        return [part.strip() for part in crumbs.split("›")]


@dataclass(frozen=True)
class AccountRow:
    id: int
    number: str
    name: str
    managed_marker: bool
    edit_enabled: bool


class AccountsScreen(DemoPage):
    ready = "accounts.table"

    def open(self) -> AccountsScreen:
        self.goto("/ui/admin/accounts")
        return self

    def rows(self) -> list[AccountRow]:
        """Every row, with its row menu opened to read whether *Edit* is enabled."""
        out = []
        for row in self.resolve("accounts.row").all():
            self.within(row, "accounts.menu").click()
            item = self.within(row, "accounts.edit")
            item.wait_for()
            enabled = item.get_attribute("aria-disabled") != "true" and item.is_enabled()
            self.within(row, "accounts.menu").click()  # close it again
            out.append(
                AccountRow(
                    id=int(row.get_attribute("data-id") or 0),
                    number=self.within(row, "accounts.number").inner_text().strip(),
                    name=self.within(row, "accounts.name").inner_text().strip(),
                    managed_marker=bool(self.within(row, "accounts.managed").inner_text().strip()),
                    edit_enabled=enabled,
                )
            )
        return out

    def open_edit(self, account_id: int) -> AccountEditScreen:
        """Follow the row menu's *Edit* item, as a user would."""
        row = self.within(self.resolve("accounts.table"), "accounts.row_by_id", id=account_id)
        self.within(row, "accounts.menu").click()
        with self.page.expect_navigation():
            self.within(row, "accounts.edit").click()
        edit = AccountEditScreen(self.page, self.base_url)
        edit.wait_ready()
        return edit


class AccountEditScreen(DemoPage):
    ready = "account_edit.number"

    def number(self) -> str:
        return read_field(self.page, path="account-number-input").raw

    def name(self) -> str:
        return read_field(self.page, path="account-name-input").raw

    def number_read_only(self) -> bool | None:
        return field_is_read_only(self.page, path="account-number-input")

    def name_read_only(self) -> bool | None:
        return field_is_read_only(self.page, path="account-name-input")


class IntegrationsScreen(DemoPage):
    ready = "integrations.root"

    def open(self) -> IntegrationsScreen:
        """Open the screen while capturing the services response its forms are built from."""
        with ResponseCapture(self.page, "/api/integrations/services") as capture:
            self.goto("/ui/admin/integrations")
            self.resolve("integrations.form", key="mailer").wait_for()
        self.services = capture
        return self

    def field(self, service: str, param: str) -> EntryState:
        return self.screen.read_input(f"integration-{service}-{param}")

    def label(self, service: str, param: str) -> str:
        """The text of the field's associated ``<label>``."""
        field = self.screen.by_test_id(f"integration-{service}-{param}").first
        return (field.evaluate("e => e.labels.length ? e.labels[0].textContent : ''") or "").strip()

    def hint(self, service: str, param: str) -> str:
        hint = self.resolve("integrations.hint", target=f"{service}-{param}")
        return hint.inner_text().strip() if hint.count() else ""


class SettlementsScreen(DemoPage):
    """Settlements: Open/Closed tabs and a party filter that narrows locally."""

    ready = "settlements.table"

    def open(self) -> SettlementsScreen:
        self.list_requests: list[str] = []
        self.page.on("request", self._on_request)
        self.goto("/ui/admin/settlements")
        return self

    def _on_request(self, request) -> None:
        if "/api/settlements" in request.url and request.method == "GET":
            self.list_requests.append(request.url)

    def close(self) -> None:
        self.page.remove_listener("request", self._on_request)

    def type_party(self, fragment: str) -> str:
        field = self.resolve("settlements.party")
        field.click()
        field.press_sequentially(fragment, delay=30)
        return field.input_value()

    def options(self) -> list[str]:
        return [t.strip() for t in self.resolve("settlements.option").all_inner_texts()]

    def choose_option(self, number: str) -> None:
        """Choose the option for a party reference number (whole number, not a prefix)."""
        option = self.resolve("settlements.option").filter(has_text=exact_number(number)).first
        with self.page.expect_response(lambda r: "/api/settlements" in r.url):
            option.click()

    def open_tab(self, name: str) -> None:
        with self.page.expect_response(lambda r: "/api/settlements" in r.url):
            self.screen.open_tab(name)

    def row_count(self) -> int:
        return self.resolve("settlements.row").count()
