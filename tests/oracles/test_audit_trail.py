"""Tests for oracles.audit_trail: export, poll, controls, code matching and the secret scan."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from oracles import audit_trail as at
from oracles.activity_export import ExportFormatError

START = datetime(2031, 6, 3, 9, 0, tzinfo=timezone.utc)
END = START + timedelta(hours=1)
ROWS = [
    {
        "id": 1,
        "application": "billing",
        "category": "EXPORT",
        "event": "REQUESTED",
        "actor": "svc-release-check",
    },
    {
        "id": 2,
        "application": "billing",
        "category": "PLAN",
        "event": "UPDATED",
        "actor": "ops-user-1",
        "detail": {"field": "price"},
    },
    {
        "id": 3,
        "application": "portal",
        "category": "PLAN",
        "event": "UPDATED_NOTE",
        "actor": "ops-user-2",
    },
]


@pytest.fixture(autouse=True)
def isolated_verdicts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VERDICTS_FILE", str(tmp_path / "verdicts.jsonl"))
    monkeypatch.delenv("COVERAGE_VERDICT_REQUIRED", raising=False)


def _zip(**entries: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, text in entries.items():
            archive.writestr(name.replace("_", "."), text)
    return buffer.getvalue()


class FakeExports:
    def __init__(self, statuses: list[str], archive: bytes | None = None):
        self.statuses = statuses
        self.archive = archive or _zip(audit_json=json.dumps(ROWS))
        self.created: list[tuple[datetime, datetime]] = []
        self.listed = 0

    def create(self, engine, start, end):
        self.created.append((start, end))
        return "exp-1"

    def list(self, engine):
        status = self.statuses[min(self.listed, len(self.statuses) - 1)]
        self.listed += 1
        return [{"id": "exp-0", "status": "COMPLETED"}, {"id": "exp-1", "status": status}]

    def download(self, engine, export_id):
        assert export_id == "exp-1"
        return self.archive


def _export(fake: FakeExports, **kw):
    clock = {"t": 0.0}
    return at.export_window(
        object(),
        START,
        END,
        create=fake.create,
        list=fake.list,
        download=fake.download,
        sleep=lambda s: clock.__setitem__("t", clock["t"] + s),
        monotonic=lambda: clock["t"],
        **kw,
    )


class TestExportWindow:
    def test_polls_until_terminal_then_parses(self):
        fake = FakeExports(["QUEUED", "RUNNING", "COMPLETED"])
        assert _export(fake) == ROWS
        assert fake.listed == 3 and fake.created == [(START, END)]

    def test_failed_export_raises(self):
        with pytest.raises(at.ExportFailed, match="FAILED"):
            _export(FakeExports(["RUNNING", "FAILED"]))

    def test_timeout_raises(self):
        with pytest.raises(at.ExportTimeout, match="RUNNING"):
            _export(FakeExports(["RUNNING"]), timeout_s=5, poll_s=2)

    def test_custom_status_vocabulary(self):
        assert _export(FakeExports(["DONE"]), terminal={"DONE", "ERROR"}, succeeded="DONE")

    def test_bad_windows(self):
        with pytest.raises(ValueError, match="naive"):
            at.export_window(
                object(), datetime(2031, 1, 1), END, create=None, list=None, download=None
            )
        with pytest.raises(ValueError, match="before it starts"):
            at.export_window(object(), END, START, create=None, list=None, download=None)


class TestReadRows:
    def test_csv_and_json_entries(self):
        archive = _zip(a_csv="id,actor\n7,ops-user-1\n", b_json=json.dumps([{"id": 8}]))
        assert at.read_export_rows(archive) == [{"id": "7", "actor": "ops-user-1"}, {"id": 8}]

    def test_bad_archives(self):
        with pytest.raises(ExportFormatError):
            at.read_export_rows(b"not a zip")
        with pytest.raises(ExportFormatError, match="no .json or .csv"):
            at.read_export_rows(_zip(a_txt="x"))
        with pytest.raises(ExportFormatError, match="list of objects"):
            at.read_export_rows(_zip(a_json='{"id": 1}'))


class TestControlsAndEvents:
    def test_own_activity_present(self):
        assert [r["id"] for r in at.require_own_activity(ROWS, "billing")] == [1, 2]

    def test_own_activity_missing_is_not_nothing_audited(self):
        with pytest.raises(at.OwnActivityMissing, match="none from 'billing'"):
            at.require_own_activity(ROWS[2:], "billing")
        with pytest.raises(at.OwnActivityMissing):
            at.require_own_activity([], "billing")

    def test_events_match_codes_exactly(self):
        assert [r["id"] for r in at.events(ROWS, "PLAN", "UPDATED")] == [2]

    def test_check_events_actor(self):
        assert at.check_events(ROWS, category="PLAN", event="UPDATED", actor="ops-user-1") == []
        problems = at.check_events(ROWS, category="PLAN", event="UPDATED", actor="someone-else")
        assert problems and "ops-user-1" in problems[0]

    def test_check_events_abstains_without_a_matching_event(self):
        with pytest.raises(pytest.skip.Exception, match="no PLAN/DELETED event"):
            at.check_events(ROWS, category="PLAN", event="DELETED", actor="x")

    def test_secret_like_content_fails_the_check(self):
        rows = [*ROWS, {"id": 4, "category": "AUTH", "event": "X", "detail": {"api_key": "k1"}}]
        problems = at.check_events(rows, category="PLAN", event="UPDATED", actor="ops-user-1")
        assert problems == ["secret-like content in ['detail.api_key']: row 4"]


@pytest.mark.parametrize(
    ("row", "hits"),
    [
        ({"password": "hunter"}, ["password"]),
        ({"access_token": "<redacted>", "secret": "", "token": "****"}, []),
        ({"headers": {"Authorization": "Bearer abc"}}, ["headers.Authorization"]),
        ({"keyboard": "en", "monkey": 1, "actor": "a"}, []),
    ],
)
def test_secret_like(row, hits):
    assert at.secret_like(row) == hits
