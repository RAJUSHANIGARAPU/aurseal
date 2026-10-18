"""List screens: subscriptions (filter chip + keyset paginator), tasks (row menus)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ui.pages.demo.base import DemoPage

SEARCH_URL = "/api/search"


@dataclass(frozen=True)
class SearchCall:
    """One search request the list made, with its JSON answer."""

    body: dict
    status: int
    result: dict


class SubscriptionList(DemoPage):
    ready = "list.table"

    def open(self, limit: int | None = None) -> SubscriptionList:
        with self.page.expect_response(lambda r: SEARCH_URL in r.url):
            self.goto("/ui/subscriptions" + (f"?limit={limit}" if limit else ""))
        return self

    def rows(self) -> list[dict[str, str]]:
        return [
            {
                "id": row.get_attribute("data-id") or "",
                "title": self.within(row, "list.title").inner_text().strip(),
                "status": self.within(row, "list.status").get_attribute("data-state") or "",
            }
            for row in self.resolve("list.row").all()
        ]

    def _search(self, action) -> SearchCall:
        with self.page.expect_response(
            lambda r: SEARCH_URL in r.url and r.request.method == "POST"
        ) as caught:
            action()
        response = caught.value
        call = SearchCall(response.request.post_data_json or {}, response.status, response.json())
        self.page.wait_for_function(
            "([table, n]) => table.querySelectorAll('tbody tr').length === n",
            arg=[self.resolve("list.table").element_handle(), len(call.result["items"])],
        )
        return call

    def filter_by(self, term: str) -> SearchCall:
        """Type ``term`` and apply it; returns the search the chip triggered."""
        self.resolve("list.filter").fill(term)
        return self._search(lambda: self.resolve("list.filter").press("Enter"))

    def chip_text(self) -> str:
        chip = self.resolve("list.chip")
        return self.text("list.chip_text") if chip.is_visible() else ""

    def next_page(self) -> SearchCall:
        return self._search(lambda: self.resolve("list.next").click())

    def previous_page(self) -> SearchCall:
        return self._search(lambda: self.resolve("list.prev").click())

    def paging_enabled(self) -> dict[str, bool]:
        return {
            "prev": self.resolve("list.prev").is_enabled(),
            "next": self.resolve("list.next").is_enabled(),
        }

    def open_row(self, index: int = 0) -> None:
        link = self.within(self.resolve("list.row").nth(index), "list.link")
        with self.page.expect_navigation():
            link.click()


class TaskList(DemoPage):
    ready = "tasks.table"

    def open(self) -> TaskList:
        self.goto("/ui/tasks")
        return self

    def zone(self) -> str:
        return self.text("tasks.zone")

    def rows(self) -> list[dict[str, str]]:
        return [
            {
                "id": row.get_attribute("data-id") or "",
                **{
                    name: self.within(row, "tasks.cell", name=name).inner_text().strip()
                    for name in ("title", "subscription", "date", "time", "state")
                },
            }
            for row in self.resolve("tasks.row").all()
        ]

    def row_by_id(self, task_id: int):
        return self.within(self.resolve("tasks.table"), "tasks.row_by_id", id=task_id)

    def open_from_menu(self, task_id: int) -> None:
        row = self.row_by_id(task_id)
        self.within(row, "tasks.menu").click()
        with self.page.expect_navigation():
            self.within(row, "tasks.open").click()

    def create(self, title: str, subscription_id: int | None = None, target: str = "") -> int:
        """Create a task through the form; returns the HTTP status of the create call."""
        self.resolve("tasks.new_title").fill(title)
        if subscription_id is not None:
            self.resolve("tasks.new_subscription").fill(str(subscription_id))
        if target:
            self.resolve("tasks.new_target").fill(target)
        with self.page.expect_response(
            lambda r: re.search(r"/api/tasks$", r.url) and r.request.method == "POST"
        ) as caught:
            with self.page.expect_navigation():
                self.resolve("tasks.create").click()
        self.wait_ready()
        return caught.value.status
