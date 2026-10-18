"""Trace -> seed fixture: filtering, body resolution, dynamic values, abandoned entities."""

from __future__ import annotations

import hashlib
import json
import zipfile

import pytest

from test_data import trace_to_fixture
from test_data.fixture_replay import Fixture
from test_data.trace_to_fixture import DATE_PLACEHOLDER, extract, main

BASE = "http://127.0.0.1:8765"


class TraceBuilder:
    def __init__(self):
        self.lines: list[str] = []
        self.resources: dict[str, bytes] = {}

    def blob(self, value) -> str:
        raw = json.dumps(value).encode()
        sha = hashlib.sha1(raw).hexdigest()  # noqa: S324 - trace resource naming, not security
        self.resources[sha] = raw
        return sha

    def call(self, method, path, body=None, response=None, status=200, inline=False, lose=False):
        request = {"method": method, "url": BASE + path, "headers": []}
        if body is not None:
            if inline:
                request["postData"] = {"mimeType": "application/json", "text": json.dumps(body)}
            else:
                sha = self.blob(body)
                if lose:
                    self.resources.pop(sha)
                request["postData"] = {"mimeType": "application/json", "_sha1": sha}
        content = {"mimeType": "application/json"}
        if response is not None:
            content["_sha1"] = self.blob(response)
        snapshot = {"request": request, "response": {"status": status, "content": content}}
        self.lines.append(json.dumps({"type": "resource-snapshot", "snapshot": snapshot}))
        return self

    def zip(self, path):
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("trace.trace", "")
            z.writestr("trace.network", "\n".join(self.lines + ["not json"]))
            for sha, raw in self.resources.items():
                z.writestr(f"resources/{sha}", raw)
        return path

    def directory(self, path):
        (path / "resources").mkdir(parents=True)
        (path / "trace.network").write_text("\n".join(self.lines))
        for sha, raw in self.resources.items():
            (path / "resources" / sha).write_bytes(raw)
        return path


def journey():
    b = TraceBuilder()
    b.call("GET", "/api/plans", response=[{"id": 1}])
    # abandoned: a customer created, then the user started over
    b.call("POST", "/api/customers", {"name": "First try"}, {"id": 40, "status": "new"})
    b.call(
        "POST", "/api/customers", {"name": "Test Person"}, {"id": 41, "status": "new"}, inline=True
    )
    b.call(
        "POST",
        "/api/subscriptions",
        {"customer_id": 41, "plan_id": 1, "start_date": "2026-03-01", "quantity": 41},
        {"id": 7, "status": "draft"},
    )
    b.call("PUT", "/api/subscriptions/7/versions/3", {"data_region": "EU-NORTH"}, {"id": 7})
    b.call(
        "POST",
        "/api/subscriptions/7/actions/submit?version=3&lang=en",
        {},
        {"id": 7, "status": "pending_payment"},
    )
    b.call("POST", "/telemetry/events", {"x": 1}, {"ok": True})
    return b


def test_a_recorded_journey_becomes_a_replayable_fixture(tmp_path):
    trace = journey().zip(tmp_path / "trace.zip")
    result = extract(trace, name="subscribe", date_fields=["start_date"])
    fx = result.fixture
    assert fx["name"] == "subscribe"
    assert fx["create"] == {
        "method": "POST",
        "path": "/api/customers",
        "body": {"name": "Test Person"},
        "capture": {"customers_id": "id"},
    }
    sub, put, submit = fx["steps"]
    assert sub["body"] == {
        "customer_id": "${customers_id}",
        "plan_id": 1,  # not created by the trace: kept literally
        "start_date": DATE_PLACEHOLDER,
        "quantity": 41,  # equal to an id, but not under an id key
    }
    assert sub["capture"] == {"subscriptions_id": "id"}
    assert put["path"] == "/api/subscriptions/${subscriptions_id}/versions/${version}"
    assert submit["path"] == (
        "/api/subscriptions/${subscriptions_id}/actions/submit?version=${version}&lang=en"
    )
    assert result.step_count == 4 and not result.partial
    assert result.terminus.endswith("-> pending_payment")
    assert result.abandoned == ["POST /api/customers (id 40)"]
    assert Fixture.from_dict(fx).create.capture == {"customers_id": "id"}  # loads as a fixture


def test_an_unpacked_trace_directory_reads_the_same(tmp_path):
    from_zip = extract(journey().zip(tmp_path / "t.zip")).fixture
    from_dir = extract(journey().directory(tmp_path / "unpacked")).fixture
    assert from_dir["steps"] == from_zip["steps"]


def test_failed_calls_and_missing_bodies_mark_coverage_partial(tmp_path):
    b = TraceBuilder()
    b.call("POST", "/api/customers", {"name": "x"}, {"error": "invalid"}, status=422)
    b.call("POST", "/api/notes", {"text": "y"}, {"id": 2}, lose=True)
    result = extract(b.zip(tmp_path / "t.zip"))
    assert result.partial
    assert any("answered 422" in w for w in result.warnings)
    assert any("is not in the trace" in w for w in result.warnings)


def test_methods_and_path_pattern_are_configurable(tmp_path):
    trace = journey().zip(tmp_path / "t.zip")
    result = extract(trace, methods=["PUT"], path_pattern=r"/versions/")
    assert result.step_count == 1 and "create" not in result.fixture


def test_nothing_matching_is_reported_not_hidden(tmp_path):
    result = extract(journey().zip(tmp_path / "t.zip"), path_pattern=r"^/nothing/")
    assert result.step_count == 0 and result.partial and result.terminus == "-"


def test_cli_writes_the_fixture_and_reports(tmp_path, capsys):
    trace = journey().zip(tmp_path / "t.zip")
    out = tmp_path / "fixtures" / "subscribe.json"
    assert main([str(trace), "--out", str(out), "--date-field", "start_date"]) == 0
    assert json.loads(out.read_text())["create"]["path"] == "/api/customers"
    printed = capsys.readouterr().out
    assert "steps: 4" in printed and "dropped abandoned entity" in printed
    assert main([str(tmp_path / "missing.zip"), "--out", str(out)]) == 2


@pytest.mark.parametrize("value", [True, False])
def test_booleans_are_never_taken_for_ids(value):
    assert trace_to_fixture._replace_literal({"id": value}, "1", "${x}") == {"id": value}
