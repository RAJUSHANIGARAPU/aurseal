"""Windowed paging: oldest first, one page per window, edges counted once, truncation flagged."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from api.clients.http_engine import ApiResponse
from oracles.data_health import GetOnlyApi, ScanReport, windowed

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def at(days: int) -> str:
    return (T0 + timedelta(days=days)).isoformat()


INVOICES = [
    {"id": "INV-1", "created_at": at(0)},
    {"id": "INV-2", "created_at": at(1)},
    {"id": "INV-3", "created_at": at(2)},  # on the edge of the first two windows
    {"id": "INV-4", "created_at": at(3)},
    {"id": "INV-5", "created_at": at(5)},
]


class FakeListing:
    """Filters inclusively on both edges (as many listings do) and caps the page."""

    def __init__(self, records, cap: int = 100, fail_from: int | None = None) -> None:
        self.records = records
        self.cap = cap
        self.fail_from = fail_from
        self.calls: list[dict] = []

    def __call__(self, path, params):
        self.calls.append(dict(params))
        if self.fail_from is not None and len(self.calls) >= self.fail_from:
            return ApiResponse(503, b"")
        low, high = params["created_from"], params["created_to"]
        hits = [r for r in self.records if low <= r["created_at"] <= high]
        page = hits[: min(self.cap, params["size"])]
        return ApiResponse(200, json.dumps({"items": page, "total": len(hits)}).encode())


def read(api, **overrides):
    settings = dict(
        start=T0,
        end=T0 + timedelta(days=6),
        window=timedelta(days=2),
        from_param="created_from",
        to_param="created_to",
        total_of=lambda body: body.get("total"),
        page_size=50,
    )
    settings.update(overrides)
    return windowed(api, "/api/invoices", **settings)


def test_windows_run_oldest_first_and_cover_the_range():
    api = FakeListing(INVOICES)
    listing = read(api)
    assert [c["created_from"] for c in api.calls] == [at(0), at(2), at(4)]
    assert api.calls[-1]["created_to"] == at(6)
    assert listing.windows == listing.requests == 3


def test_an_edge_record_returned_by_two_windows_is_counted_once():
    listing = read(FakeListing(INVOICES))
    assert [r["id"] for r in listing.rows] == ["INV-1", "INV-2", "INV-3", "INV-4", "INV-5"]
    assert listing.complete


def test_a_window_reporting_more_than_it_returned_is_flagged():
    report = ScanReport()
    listing = read(FakeListing(INVOICES, cap=2), report=report)
    assert not listing.complete and not report.complete
    assert "reports 3 record(s), read 2" in listing.notes[0]
    assert report.requests == 3


def test_a_failed_window_stops_and_is_noted():
    listing = read(FakeListing(INVOICES, fail_from=2))
    assert not listing.complete and "HTTP 503" in listing.notes[0]
    assert [r["id"] for r in listing.rows] == ["INV-1", "INV-2", "INV-3"]


def test_get_only_api_is_accepted():
    api = FakeListing(INVOICES)
    assert len(read(GetOnlyApi(api)).rows) == 5


def test_window_must_be_positive():
    with pytest.raises(ValueError):
        read(FakeListing(INVOICES), window=timedelta(0))
