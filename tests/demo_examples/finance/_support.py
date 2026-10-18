"""Seeding and reading helpers shared by the finance examples (all values invented)."""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from api.clients.http_engine import HttpEngine
from test_data.fixture_replay import Fixture, FixtureSeeder

ROOT = Path(__file__).resolve().parents[3]
PROCESS_FIXTURE = ROOT / "test_data" / "fixtures" / "demo" / "process_invoices.json"

FROZEN_NOW = "2026-03-10T09:00:00Z"
START_DATE = "2026-03-01"
BOOK_UNTIL = "2026-04-15"
CANCEL_EFFECTIVE = "2026-04-20"

GRID_COLUMNS = ("kind", "period_start", "period_end", "net", "tax", "fee", "total", "state")


def process_due(engine: HttpEngine, subscription_id: int, until: str) -> list[int]:
    """Book a subscription's scheduled invoices due by ``until`` by replaying the API fixture.

    The booking precondition for cancellation, change and renewal examples: booked periods
    are what those flows settle against.
    """
    result = FixtureSeeder(engine).replay(
        Fixture.load(PROCESS_FIXTURE), {"subscription_id": subscription_id, "until": until}
    )
    return list(result["booked"])


class _GridParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[dict[str, str]] = []
        self.in_grid = False
        self._row: dict[str, str] | None = None
        self._col: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "table" and a.get("data-testid") == "ledger-grid":
            self.in_grid = True
        elif self.in_grid and tag == "tr" and a.get("data-testid") == "ledger-row":
            self._row = {"row_state": a.get("data-state") or ""}
        elif self._row is not None and tag == "td":
            self._col = a.get("data-col")

    def handle_endtag(self, tag: str) -> None:
        if tag == "td":
            self._col = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None
        elif tag == "table":
            self.in_grid = False

    def handle_data(self, data: str) -> None:
        if self._row is not None and self._col:
            self._row[self._col] = self._row.get(self._col, "") + data.strip()


def ledger_grid_rows(html: str) -> list[dict[str, str]]:
    """Rows of the ``ledger-grid`` table as ``{column: text}`` plus the row's ``data-state``."""
    parser = _GridParser()
    parser.feed(html)
    return parser.rows


def grid_row_of(transaction: dict[str, Any]) -> dict[str, str]:
    """How the grid must render one API transaction."""
    return {
        "kind": transaction["kind"],
        "period_start": transaction["period_start"],
        "period_end": transaction["period_end"],
        "net": f"{transaction['net']:.2f}",
        "tax": f"{transaction['tax']:.2f}",
        "fee": f"{transaction['fee_net']:.2f}",
        "total": f"{transaction['total']:.2f}",
        "state": transaction["state"],
    }
