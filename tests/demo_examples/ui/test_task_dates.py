"""Task target instants shown as local date and time, checked against a hand-written table."""

from __future__ import annotations

import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from ui.pages.demo.lists import TaskList

ZONE = "Europe/Berlin"

#: (case, UTC instant, local date, local HH:mm) - written by hand, never computed
ROWS = [
    ("late evening crosses midnight", "2026-08-23T22:40:00Z", "2026-08-24", "00:40"),
    ("shortly after midnight UTC", "2026-08-24T00:20:00Z", "2026-08-24", "02:20"),
    ("summer midday", "2026-06-15T10:05:00Z", "2026-06-15", "12:05"),
    ("before the autumn switch", "2026-10-25T00:45:00Z", "2026-10-25", "02:45"),
    ("after the autumn switch", "2026-10-25T01:45:00Z", "2026-10-25", "02:45"),
    ("before the spring switch", "2027-03-28T00:40:00Z", "2027-03-28", "01:40"),
    ("after the spring switch", "2027-03-28T01:40:00Z", "2027-03-28", "03:40"),
    ("winter midnight crossing", "2026-01-15T23:10:00Z", "2026-01-16", "00:10"),
    ("year end", "2026-12-31T23:45:00Z", "2027-01-01", "00:45"),
]


def _utc(instant: str) -> datetime:
    return datetime.fromisoformat(instant.replace("Z", "+00:00"))


def test_row_table_matches_tz_database():
    """Offline (no browser): every hand-written row agrees with the tz database."""
    for case, instant, day, hm in ROWS:
        local = _utc(instant).astimezone(ZoneInfo(ZONE))
        assert (local.strftime("%Y-%m-%d"), local.strftime("%H:%M")) == (day, hm), case
        assert hm != _utc(instant).strftime("%H:%M"), f"{case}: local time equals UTC"
    crossing = [r for r in ROWS if r[2] != r[1][:10]]
    same_day = [r for r in ROWS if r[2] == r[1][:10]]
    assert crossing and same_day, "the table needs date-crossing and same-day rows"


@pytest.mark.demo_ui
@pytest.mark.parametrize(("case", "instant", "day", "hm"), ROWS, ids=[r[0] for r in ROWS])
def test_task_target_rendered_in_local_zone(
    demo_page, demo_sut, demo_control, case, instant, day, hm
):
    demo_control.settings(ui_timezone=ZONE)
    title = f"Due {uuid.uuid4().hex[:8]}"
    task = demo_sut.client.ok("POST", "/api/tasks", {"title": title, "target_at": instant})
    # control: the stored instant is the one sent, so a wrong screen value is the screen's
    assert _utc(task["target_at"]) == _utc(instant) and task["state"] == "OPEN"

    tasks = TaskList(demo_page, demo_sut.base_url).open()
    assert tasks.zone() == ZONE
    (row,) = [r for r in tasks.rows() if r["id"] == str(task["id"])]
    assert row["title"] == title
    assert (row["date"], row["time"]) == (day, hm), f"{case}: {instant} shown as {row}"
