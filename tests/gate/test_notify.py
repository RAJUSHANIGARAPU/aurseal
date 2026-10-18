"""Chat notification for a red or not-evidence run (gate.notify), against a loopback fake."""

from __future__ import annotations

import io
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from gate.notify import (
    FAILED,
    NO_VERDICT,
    NOT_EVIDENCE,
    decide,
    describe,
    main,
    message,
    send,
    truncate,
)

MARKER = "VERDICT=FAIL ENFORCEMENT=UNSTABLE RUN=r1 LANE=browser"


def test_description():
    line = describe([MARKER], "qa", "0123456789abcdef", prefix="nightly")
    assert line == f"nightly | {MARKER} | env qa | config 01234567"
    assert describe([], "qa", None) == f"{NO_VERDICT} | env qa | config not bound"


@pytest.mark.parametrize(
    ("results", "verdicts", "event", "ref", "notify_all", "expected"),
    [
        ({"a": "failure"}, [], "push", "refs/heads/main", False, FAILED),
        ({"a": "success"}, [("FAIL", "unstable")], "push", "refs/heads/main", False, NOT_EVIDENCE),
        ({"a": "cancelled"}, [], "push", "refs/heads/main", False, NOT_EVIDENCE),
        ({"a": "success"}, [("PASS", "fail")], "push", "refs/heads/main", False, None),
        ({"a": "failure"}, [], "push", "refs/heads/feat/x", False, None),
        ({"a": "failure"}, [], "push", "refs/heads/feat/x", True, FAILED),
        ({"a": "failure"}, [], "schedule", "refs/heads/other", False, FAILED),
        ({"a": "success"}, [("FAIL", "fail")], "push", "refs/heads/main", False, None),
    ],
)
def test_decision(results, verdicts, event, ref, notify_all, expected):
    assert decide(results, verdicts, event=event, ref=ref, notify_all=notify_all) == expected


class _Hook(BaseHTTPRequestHandler):
    statuses: list[int] = []
    bodies: list[dict] = []

    def do_POST(self):  # noqa: N802 - stdlib name
        length = int(self.headers.get("Content-Length", 0))
        type(self).bodies.append(json.loads(self.rfile.read(length)))
        status = type(self).statuses.pop(0) if type(self).statuses else 200
        self.send_response(status)
        self.end_headers()

    def log_message(self, *args):  # silence
        return


@pytest.fixture
def hook():
    _Hook.statuses, _Hook.bodies = [], []
    server = HTTPServer(("127.0.0.1", 0), _Hook)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield _Hook, f"http://127.0.0.1:{server.server_port}/hooks/very-secret-path"
    server.shutdown()
    server.server_close()


def test_payload_and_retry_after_a_5xx(hook):
    handler, url = hook
    handler.statuses = [500, 200]
    environ = {
        "GITHUB_REPOSITORY": "o/r",
        "GITHUB_REF_NAME": "main",
        "GITHUB_SERVER_URL": "https://github.example",
        "GITHUB_RUN_ID": "42",
    }
    text = message(FAILED, describe([MARKER], "qa", None), environ)
    out = io.StringIO()
    assert send(url, text, out=out) is True
    assert len(handler.bodies) == 2
    body = handler.bodies[-1]["text"]
    assert body.startswith("[failed] o/r main:") and MARKER in body
    assert body.endswith("https://github.example/o/r/actions/runs/42")
    assert url not in out.getvalue()


def test_two_5xx_warn_and_do_not_fail(hook):
    handler, url = hook
    handler.statuses = [502, 503]
    out = io.StringIO()
    assert send(url, "x", out=out) is False
    assert "::warning title=Notification::not delivered (HTTP 503)" in out.getvalue()
    assert url not in out.getvalue()


def test_unset_url_makes_no_request(capsys):
    calls = []
    assert send(None, "x", opener=lambda *a, **k: calls.append(a)) is False
    assert calls == [] and "notification not configured" in capsys.readouterr().out


def test_the_url_never_leaks_from_a_connection_error(capsys):
    url = "http://127.0.0.1:1/hooks/secret-part"

    def refuse(request, timeout):
        raise OSError(f"cannot connect to {url}")

    out = io.StringIO()
    send(url, "x", opener=refuse, out=out)
    assert "secret-part" not in out.getvalue() and "<webhook>" in out.getvalue()


def test_truncation():
    assert truncate("a" * 600, 500) == "a" * 499 + "…"
    assert truncate("short", 500) == "short"


def test_cli_send_without_a_webhook_passes(tmp_path, capsys):
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "run-trust-browser.txt").write_text(MARKER + "\n")
    environ = {"GITHUB_EVENT_NAME": "push", "GITHUB_REF": "refs/heads/main"}
    needs = json.dumps({"browser": {"result": "success"}})
    code = main(["send", "--reports", str(reports), "--needs-json", needs], environ=environ)
    assert code == 0 and "notification not configured" in capsys.readouterr().out


def test_cli_describe_exports_the_line(tmp_path, capsys):
    output = tmp_path / "out"
    code = main(
        ["describe", "--reports", str(tmp_path), "--github-output", str(output)], environ={}
    )
    assert code == 0 and f"description={NO_VERDICT}" in output.read_text()
