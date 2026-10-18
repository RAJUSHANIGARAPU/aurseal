"""Exploratory sessions: notes, debrief, timebox, and a contribution that is never proof."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from qaplatform import exploratory
from qaplatform.capture import CaptureHandle
from qaplatform.errors import Conflict
from tests.qaplatform._helpers import Api, token_for

START = {
    "schema_version": "1.0",
    "charter": "Explore refunds with odd amounts",
    "area": "billing",
    "requirements": ["REQ-13"],
    "timebox_min": 30,
}


@pytest.fixture
def tester(service, store):
    return Api(service, token_for(store, "tess", "tester"))


def _start(api, **extra):
    resp = api("POST", "/projects/default/sessions", {**START, **extra})
    assert resp.status == 201, resp.payload
    return resp.payload


def _note(api, sid, kind, text, **extra):
    return api(
        "POST",
        f"/projects/default/sessions/{sid}/notes",
        {"schema_version": "1.0", "kind": kind, "text": text, **extra},
    )


def test_session_lifecycle(tester, api):
    doc = _start(tester)
    sid = doc["id"]
    assert doc["status"] == "active" and doc["run_id"].startswith("r-")
    _note(tester, sid, "observation", "refund page loads slowly")
    _note(tester, sid, "bug", "negative refund accepted", defect_key="BUG-77")
    _note(
        tester,
        sid,
        "question",
        "is a zero refund allowed?",
        attachment_uri="https://files.example.test/zero.png",
    )
    ended = tester(
        "POST",
        f"/projects/default/sessions/{sid}/end",
        {"schema_version": "1.0", "debrief": "covered amounts; not currencies"},
    ).payload
    assert ended["status"] == "ended"
    assert [n["seq"] for n in ended["notes"]] == [1, 2, 3]
    assert ended["bugs"] == [{"seq": 2, "text": "negative refund accepted", "defect_key": "BUG-77"}]
    assert ended["note_counts"] == {"observation": 1, "question": 1, "bug": 1, "idea": 0}
    run = api("GET", f"/projects/default/runs/{doc['run_id']}").payload
    assert run["source"] == "exploratory" and run["status"] == "finished"
    assert run["integrity"]["verdict"] == "UNJUDGED"  # a session is not a test run
    evidence = api("GET", f"/projects/default/runs/{doc['run_id']}/evidence").payload["links"]
    assert evidence[0]["uri"] == "https://files.example.test/zero.png"


def test_contribution_is_recorded_never_proved(tester, api):
    sid = _start(tester)["id"]
    tester("POST", f"/projects/default/sessions/{sid}/end", {"schema_version": "1.0"})
    rows = api("GET", "/projects/default/verdicts", query={"kind": "coverage"}).payload
    assert [(v["subject"], v["verdict"]) for v in rows["verdicts"]] == [("REQ-13", "recorded")]
    trace = api("GET", "/projects/default/requirements/REQ-13").payload
    assert trace["status"] == "recorded_only"
    assert trace["tests"][0]["test_id"] == f"session::{sid}"


def test_abandoned_session_records_nothing(tester, api):
    sid = _start(tester)["id"]
    out = tester(
        "POST",
        f"/projects/default/sessions/{sid}/end",
        {"schema_version": "1.0", "abandoned": True},
    ).payload
    assert out["status"] == "abandoned"
    assert api("GET", "/projects/default/verdicts").payload["verdicts"] == []


def test_ending_twice_and_notes_after_the_end_are_refused(service, store, tester):
    sid = _start(tester)["id"]
    p = service.authenticate(tester.token)
    service.end_session(p, "default", sid, {"schema_version": "1.0"})
    with pytest.raises(Conflict):
        service.end_session(p, "default", sid, {"schema_version": "1.0"})
    assert _note(tester, sid, "idea", "too late").status == 409


def test_over_timebox_is_flagged_not_refused(service, store, tester):
    sid = _start(tester, timebox_min=5)["id"]
    later = datetime.now(timezone.utc) + timedelta(minutes=12)
    service.clock = lambda: later
    p = service.authenticate(tester.token)
    doc = service.get_session(p, "default", sid)
    assert doc["over_timebox"] is True and doc["duration_min"] >= 11
    assert (
        service.add_session_note(
            p, "default", sid, {"schema_version": "1.0", "kind": "idea", "text": "still allowed"}
        )["seq"]
        == 1
    )


def test_sessions_by_requirement_and_viewer_limits(service, store, tester):
    _start(tester)
    _start(tester, requirements=["REQ-99"])
    listed = tester("GET", "/projects/default/sessions", query={"requirement": "REQ-99"})
    assert [s["requirements"] for s in listed.payload["sessions"]] == [["REQ-99"]]
    viewer = Api(service, token_for(store, "vic", "viewer"))
    assert viewer("POST", "/projects/default/sessions", START).status == 403
    assert viewer("GET", "/projects/default/sessions").status == 200


def test_summary_rules():
    now = datetime(2026, 1, 5, 11, 0, tzinfo=timezone.utc)
    s = {
        "id": "xs-1",
        "run_id": "r-1",
        "charter": "c",
        "tester_user_id": "t",
        "status": "active",
        "started_at": "2026-01-05T10:00:00Z",
        "timebox_min": 45,
        "requirements": [],
    }
    doc = exploratory.summary(s, [], now)
    assert (doc["duration_min"], doc["over_timebox"]) == (60.0, True)
    assert exploratory.summary({**s, "timebox_min": None}, [], now)["over_timebox"] is False


# ---------------------------------------------------------------- browser capture helper


class FakeTracing:
    def __init__(self, fail_stop: bool = False) -> None:
        self.started = False
        self.fail_stop = fail_stop

    def start(self, **kw):
        self.started = True

    def stop(self, path=None):
        if self.fail_stop:
            raise RuntimeError("browser has been closed")
        with open(path, "wb") as fh:
            fh.write(b"PK")


class FakePage:
    def __init__(self) -> None:
        self.handlers: dict[str, list] = {}

    def on(self, event, handler):
        self.handlers.setdefault(event, []).append(handler)

    def emit(self, event, arg):
        for h in self.handlers.get(event, []):
            h(arg)


class FakeContext:
    def __init__(self, fail_stop: bool = False) -> None:
        self.tracing = FakeTracing(fail_stop)
        self.pages = [FakePage()]
        self.handlers: dict[str, list] = {}

    def on(self, event, handler):
        self.handlers.setdefault(event, []).append(handler)

    def new_page(self):
        page = FakePage()
        for h in self.handlers.get("page", []):
            h(page)
        return page


class Msg:
    def __init__(self, type_, text):
        self.type, self.text = type_, text


class Resp:
    def __init__(self, status, url):
        self.status, self.url = status, url


def test_capture_attaches_trace_and_errors(tmp_path):
    notes: list[dict] = []
    ctx = FakeContext()
    handle = CaptureHandle(ctx, "xs-1", notes.append, artifact_dir=tmp_path).start()
    assert ctx.tracing.started
    first = ctx.pages[0]
    first.emit("console", Msg("log", "fine"))
    first.emit("console", Msg("error", "TypeError: x is undefined"))
    later = ctx.new_page()
    later.emit("response", Resp(503, "https://app.example.test/api/refunds"))
    later.emit("response", Resp(200, "https://app.example.test/"))
    handle.close()
    texts = [n["text"] for n in notes]
    assert texts[0] == "browser console error: TypeError: x is undefined"
    assert texts[1] == "browser HTTP 503 from https://app.example.test/api/refunds"
    assert notes[-1]["attachment_uri"].endswith("session-xs-1.zip")
    assert (tmp_path / "session-xs-1.zip").read_bytes() == b"PK"
    assert handle.close() == []  # closing again posts nothing


def test_a_crash_during_capture_still_posts_what_exists(tmp_path):
    notes: list[dict] = []
    ctx = FakeContext(fail_stop=True)
    with pytest.raises(RuntimeError, match="tester's script broke"):
        with CaptureHandle(ctx, "xs-2", notes.append, artifact_dir=tmp_path).start():
            ctx.pages[0].emit("pageerror", "ReferenceError: y")
            raise RuntimeError("tester's script broke")
    assert notes[0]["text"] == "browser page error: ReferenceError: y"
    assert notes[-1]["text"].startswith("trace not saved: RuntimeError")


def test_capture_caps_error_notes(tmp_path):
    notes: list[dict] = []
    handle = CaptureHandle(
        FakeContext(), "xs-3", notes.append, artifact_dir=tmp_path, max_errors=2
    ).start()
    for i in range(5):
        handle.context.pages[0].emit("console", Msg("error", f"e{i}"))
    handle.close()
    assert [n["text"] for n in notes][:3] == [
        "browser console error: e0",
        "browser console error: e1",
        "3 more browser error(s) not posted; see the trace",
    ]


def test_capture_notes_land_in_the_session(service, store, tester, tmp_path):
    from qaplatform.capture import open_capture
    from qaplatform.client import PlatformClient
    from tests.qaplatform._helpers import InProcess

    sid = _start(tester)["id"]
    client = PlatformClient(
        "http://p.example.test", tester.token, session=InProcess(service), backoff_s=0
    )
    ctx = FakeContext()
    with open_capture(client, sid, ctx, artifact_dir=tmp_path):
        ctx.pages[0].emit("console", Msg("error", "boom"))
    doc = client.get_session(sid)
    assert [n["text"] for n in doc["notes"]][0] == "browser console error: boom"
    assert doc["notes"][-1]["attachment_uri"].endswith(f"session-{sid}.zip")


def test_cli_session_commands(monkeypatch, service, tester, capsys):
    from qaplatform import cli
    from qaplatform.client import PlatformClient
    from tests.qaplatform._helpers import InProcess

    client = PlatformClient(
        "http://p.example.test", tester.token, session=InProcess(service), backoff_s=0
    )
    monkeypatch.setattr(cli, "_client", lambda args: client)
    assert cli.main(["session", "start", "--charter", "poke at search", "--req", "REQ-4"]) == 0
    sid = capsys.readouterr().out.split()[1]
    assert cli.main(["session", "note", "--session", sid, "results jump", "--defect", "BUG-5"]) == 0
    assert "bug" in capsys.readouterr().out
    assert cli.main(["session", "end", "--session", sid, "--debrief", "done"]) == 0
    assert "1 bug note(s)" in capsys.readouterr().out
