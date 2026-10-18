"""The opt-in data-health entry point, against an in-test fake environment.

The fake serves listings seeded with the defects a shared environment
accumulates: unparseable dates, stale drafts and rejected transactions. The
scan must report them, write the Markdown/JSON report, and send only GETs.
"""

from __future__ import annotations

import ast
import inspect
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from api.clients.http_engine import HttpEngine
from oracles import data_health as dh
from tests.api_layer.fakes import FakeAdapter, make_response, session_with


def uuid7(at: datetime) -> str:
    ms = int(at.timestamp() * 1000)
    return str(uuid.UUID(int=(ms << 80) | (0x7 << 76) | (0x2 << 62) | 0x99))


NOW = datetime.now(timezone.utc)

SUBSCRIPTIONS = [
    {
        "id": "SUB-000001",
        "start": "2031-05-04T00:00:00.000Z",
        "status": "active",
        "created_by": "bot@example.test",
        "draft": None,
    },
    {
        "id": "SUB-000002",
        "start": "2031-05-04",
        "status": "active",
        "created_by": "pat@example.test",
        "draft": uuid7(NOW - timedelta(days=9)),
    },
    {"id": "SUB-000003", "start": "04/05/2031", "status": "active", "draft": None},
]
TRANSACTIONS = [
    {
        "id": "TX-1",
        "start": "2031-05-04T00:00:00.000Z",
        "status": "rejected",
        "created_by": "bot@example.test",
    },
    {"id": "TX-2", "start": "2031-05-04T00:00:00.000Z", "status": "booked"},
]

ENV = {
    "TEST_ENV": "demo",
    "DATA_HEALTH_KEYS": "subscriptions=api/subscriptions,transactions=api/transactions",
    "DATA_HEALTH_DATETIME_FIELDS": "start",
    "DATA_HEALTH_BAD_STATUS": "status=rejected|failed",
    "DATA_HEALTH_DRAFT_FIELD": "draft",
    "DATA_HEALTH_AUTOMATION_ACCOUNTS": "bot@example.test",
}


def _listing(records):
    return lambda request: make_response(200, {"items": records})


@pytest.fixture
def fake_env():
    adapter = (
        FakeAdapter()
        .on("GET", "/api/subscriptions", _listing(SUBSCRIPTIONS))
        .on("GET", "/api/transactions", _listing(TRANSACTIONS))
    )
    api = dh.GetOnlyApi.over(HttpEngine("https://sut.example.test/", session=session_with(adapter)))
    return adapter, api


def test_report_written_with_unverified_column(tmp_path, fake_env):
    adapter, api = fake_env
    code = dh.main([], api=api, environ=ENV, report_dir=tmp_path)
    assert code == dh.EXIT_OK  # findings are reported, not asserted
    md = (tmp_path / "demo.md").read_text()
    assert "| Rule | Findings | Unverified |" in md
    assert "| subscriptions | 3 |" in md and "| transactions | 2 |" in md
    data = json.loads((tmp_path / "demo.json").read_text())
    rules = data["rules"]
    assert rules["datetime-precision"]["count"] == 1  # 04/05/2031 is no date-time
    assert rules["date-without-time"]["count"] == 1  # 2031-05-04 in a date-time field
    assert rules["bad-status"]["count"] == 1 and rules["bad-status"]["by_automation"] == 1
    assert rules["stale"]["count"] == 1 and rules["stale"]["by_others"] == 1
    assert data["requests"] == 2 and data["complete"]
    assert {r.method for r in adapter.sent} == {"GET"}


def test_named_rule_gates_the_run(tmp_path, fake_env):
    _, api = fake_env
    code = dh.main(["--fail-on", "stale"], api=api, environ=ENV, report_dir=tmp_path)
    assert code == dh.EXIT_GATED
    assert dh.main(["--fail-on", "all"], api=api, environ=ENV, report_dir=tmp_path) == 1


def test_stopped_scan_is_not_a_result(tmp_path):
    adapter = FakeAdapter().on("GET", "/api/subscriptions", make_response(503, b"down"))
    api = dh.GetOnlyApi.over(HttpEngine("https://sut.example.test/", session=session_with(adapter)))
    code = dh.main([], api=api, environ=ENV, report_dir=tmp_path)
    assert code == dh.EXIT_STOPPED
    assert len(adapter.sent) == 1  # stopped at once, the second key was never read
    assert "SCAN ABORTED" in (tmp_path / "demo.md").read_text()


def test_unreadable_listing_stops(tmp_path):
    adapter = FakeAdapter().on("GET", "/api/", make_response(200, b"<html>login</html>"))
    api = dh.GetOnlyApi.over(HttpEngine("https://sut.example.test/", session=session_with(adapter)))
    assert dh.main([], api=api, environ=ENV, report_dir=tmp_path) == dh.EXIT_STOPPED


def test_settings_and_rule_configuration():
    s = dh.ScanSettings.from_env({**ENV, "DATA_HEALTH_MAX_PAGES": "3"})
    assert s.keys == {"subscriptions": "api/subscriptions", "transactions": "api/transactions"}
    assert s.max_pages == 3 and s.automation_accounts == ("bot@example.test",)
    assert dh.parse_keys("orders") == {"orders": "orders"}
    with pytest.raises(ValueError, match="DATA_HEALTH_KEYS"):
        dh.ScanSettings.from_env({})
    with pytest.raises(ValueError, match="no data-health rule configured"):
        dh.default_rules({})
    assert [r.id for r in dh.default_rules(ENV)] == [
        "datetime-precision",
        "date-without-time",
        "bad-status",
        "stale",
    ]


def test_unknown_fail_on_rule_is_refused(fake_env):
    _, api = fake_env
    settings = dh.ScanSettings.from_env({**ENV, "DATA_HEALTH_FAIL_ON": "typo"})
    with pytest.raises(ValueError, match="not scanned"):
        dh.run_scan(api, dh.default_rules(ENV), settings)


def test_missing_base_url_without_api(tmp_path):
    with pytest.raises(SystemExit, match="DATA_HEALTH_BASE_URL"):
        dh.main([], environ=ENV, report_dir=tmp_path)


def test_entry_point_calls_no_writing_verb():
    tree = ast.parse(inspect.getsource(dh))
    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert not calls & {"post", "put", "patch", "delete", "request", "post_multipart"}
