"""
The subscription detail screen: status, version selector, lifecycle actions, the
ledger grid, document and message downloads, background message generation, notes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from decimal import Decimal

from ui.components.field_reader import read_field
from ui.components.response_capture import ResponseCapture
from ui.components.submit_outcome import SubmitResult, submit_and_capture
from ui.components.value_entry import parse_amount
from ui.pages.demo.base import DemoPage
from ui.pages.demo.draft_screen import DraftScreen

LEDGER_COLUMNS = ("kind", "period_start", "period_end", "net", "tax", "fee", "total", "state")
AMOUNT_COLUMNS = frozenset({"net", "tax", "fee", "total"})


@dataclass(frozen=True)
class Download:
    """What a browser download delivered: the name it suggested and its bytes."""

    suggested_name: str
    data: bytes

    @property
    def size(self) -> int:
        return len(self.data)


def ledger_row(cells: dict[str, str]) -> dict[str, object]:
    """One grid row as typed values: amounts as ``Decimal``, everything else as text."""
    return {
        key: parse_amount(value) if key in AMOUNT_COLUMNS else value.strip()
        for key, value in cells.items()
    }


class SubscriptionDetail(DemoPage):
    ready = "detail.version_panel"

    def open(self, subscription_id: int, version: int | None = None) -> SubscriptionDetail:
        """Deep link to the detail page (optionally one version) and wait for it to load."""
        query = f"?version={version}" if version is not None else ""
        self.goto(f"/ui/subscriptions/{subscription_id}{query}")
        self.wait_loaded()
        return self

    def wait_loaded(self) -> None:
        """The version fields are filled by the page's own API call; wait for it."""
        self.wait_ready()
        self.resolve("detail.field", name="version-number").filter(
            has_text=re.compile(r"\d")
        ).wait_for()

    # -- reading ---------------------------------------------------------------------------

    def status(self) -> str:
        return self.resolve("detail.status_pill").first.get_attribute("data-state") or ""

    def number(self) -> str:
        return self.text("detail.number")

    def field(self, name: str) -> str:
        """A read-only detail value by its test id (``holder-name``, ``term-end``, ...)."""
        return read_field(self.page, path=name).raw

    def section_visible(self, name: str) -> bool:
        """A named page section (``Ledger``, ``Documents``, ``Messages``, ``Notes``) is shown."""
        return self.resolve("detail.section", name=name).first.is_visible()

    def status_contains(self, text: str) -> bool:
        return text.lower() in self.status().lower()

    def is_cancelled(self) -> bool:
        return self.status() == "cancelled"

    def current_version(self) -> int:
        return int(self.field("version-number"))

    def ledger_kinds(self) -> list[str]:
        return [str(row["kind"]) for row in self.ledger()]

    def version_is_current(self) -> bool:
        return self.resolve("detail.version_panel").get_attribute("data-current") == "true"

    def subscription_id(self) -> int:
        match = re.search(r"/ui/subscriptions/(\d+)", self.page.url)
        if not match:
            raise AssertionError(f"not on a detail page: {self.page.url}")
        return int(match.group(1))

    def actions(self) -> list[str]:
        return [
            button.get_attribute("data-action") or ""
            for button in self.within(self.resolve("detail.actions"), "detail.any_action").all()
        ]

    # -- ledger grid -------------------------------------------------------------------------

    def ledger(self) -> list[dict[str, object]]:
        rows = []
        for row in self.resolve("detail.ledger_row").all():
            cells = {
                column: self.within(row, "detail.ledger_cell", column=column).inner_text()
                for column in LEDGER_COLUMNS
            }
            rows.append(ledger_row(cells))
        return rows

    # -- versions ----------------------------------------------------------------------------

    def version_options(self) -> list[str]:
        options = self.resolve("detail.version_select").get_by_role("option")
        return [t.strip() for t in options.all_inner_texts()]

    def select_version(self, version: int) -> ResponseCapture:
        """Pick a version; returns the capture of the version GET the new page makes."""
        pattern = re.compile(rf"/api/subscriptions/\d+\?version={version}$")
        capture = ResponseCapture(self.page, pattern)
        with capture:
            with self.page.expect_response(lambda r: bool(pattern.search(r.url))):
                self.resolve("detail.version_select").select_option(str(version))
            self.wait_loaded()
        return capture

    # -- actions ------------------------------------------------------------------------------

    def start(self, action: str) -> SubmitResult:
        """Click a lifecycle action; change/cancel/renew/note open a draft screen."""
        return submit_and_capture(
            self.page,
            lambda: self.resolve("detail.action", action=action).click(),
            re.compile(r"/api/subscriptions/\d+/(drafts|actions/\w+)$"),
            advanced=lambda page: "/ui/drafts/" in page.url,
            settle_ms=4000,
        )

    def start_discarding_a_stale_draft(self, action: str) -> tuple[SubmitResult, bool]:
        """
        Start ``action``; when an abandoned draft blocks it (the action is withheld, or
        answers 409), discard that draft through its own screen and start once more.
        Returns the final outcome and whether a draft was discarded.
        """
        blocked = self.resolve("detail.action", action=action).count() == 0
        if not blocked:
            first = self.start(action)
            if first.status != 409:
                return first, False
        if not self.resolve("detail.open_draft_link").count():
            raise AssertionError(f"'{action}' is not offered and no open draft explains why")
        with self.page.expect_navigation():
            self.resolve("detail.open_draft_link").click()
        stale = DraftScreen(self.page, self.base_url)
        stale.wait_ready()
        stale.discard()
        self.wait_loaded()
        return self.start(action), True

    def draft_id(self) -> int:
        match = re.search(r"/ui/drafts/(\d+)", self.page.url)
        if not match:
            raise AssertionError(f"no draft screen open: {self.page.url}")
        return int(match.group(1))

    # -- documents and messages -----------------------------------------------------------------

    def _download(self, link) -> Download:
        with self.page.expect_download() as caught:
            link.click()
        download = caught.value
        path = download.path()
        data = open(path, "rb").read() if path else b""
        return Download(download.suggested_filename, data)

    def document_rows(self) -> int:
        return self.resolve("detail.document_row").count()

    def download_document(self, index: int = 0) -> Download:
        return self._download(self.resolve("detail.download_document").nth(index))

    def document_templates(self) -> list[str]:
        return [
            (self.within(row, "detail.document_template").first.inner_text() or "").strip()
            for row in self.resolve("detail.document_row").all()
        ]

    def download_document_for(self, template: str) -> Download:
        """Download the newest document rendered from ``template``."""
        templates = self.document_templates()
        if template not in templates:
            raise AssertionError(f"no {template!r} document; listed: {templates}")
        index = len(templates) - 1 - templates[::-1].index(template)
        return self.download_document(index)

    def message_rows(self) -> list[dict[str, str]]:
        return [
            {
                "id": row.get_attribute("data-id") or "",
                "status": row.get_attribute("data-status") or "",
            }
            for row in self.resolve("detail.message_row").all()
        ]

    def download_message(self, index: int = 0) -> Download:
        return self._download(self.resolve("detail.download_message").nth(index))

    def generate_messages(self, timeout_ms: int = 15000) -> tuple[SubmitResult, list[str]]:
        """
        Start message generation and follow its polling to the end.

        Returns the POST outcome and every poll URL the page requested, in order.
        """
        polls: list[str] = []
        started: dict[str, str] = {}

        def on_request(request) -> None:
            if "/api/jobs/" in request.url:
                polls.append(request.url)

        # The page navigates as soon as the job is done, which can be before the start call's
        # body is read afterwards; take the body as the call returns instead.
        def keep_start_body(route) -> None:
            fetched = route.fetch()
            started["body"] = fetched.text()
            route.fulfill(response=fetched)

        self.page.on("request", on_request)
        self.page.route("**/api/messages/generate", keep_start_body)
        try:
            with self.page.expect_navigation(timeout=timeout_ms):
                outcome = submit_and_capture(
                    self.page,
                    lambda: self.resolve("detail.generate_messages").click(),
                    "/api/messages/generate",
                    settle_ms=0,
                )
            self.wait_loaded()
        finally:
            self.page.unroute("**/api/messages/generate", keep_start_body)
            self.page.remove_listener("request", on_request)
        if not outcome.body and "body" in started:
            outcome = replace(outcome, body=started["body"])
        return outcome, polls

    def job_status(self) -> str:
        return self.text("detail.job_status")

    def notes(self) -> list[tuple[str, str]]:
        return [
            (
                self.within(item, "detail.note_title").inner_text().strip(),
                self.within(item, "detail.note_description").inner_text().strip(),
            )
            for item in self.resolve("detail.note_item").all()
        ]

    def monthly_gross(self) -> Decimal | None:
        return parse_amount(self.field("monthly-gross"))
