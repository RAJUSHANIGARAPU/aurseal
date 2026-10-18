"""The read-only CI reader against fake GitHub and storage servers on loopback."""

from __future__ import annotations

import io
import json
import socket
import subprocess
import sys
import threading
import time
import zipfile
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from tools import ci_reader as cr

ROOT = Path(__file__).resolve().parents[2]
REPO = "octo/demo-repo"
TOKEN = "ghp_faketoken1234567890"
SIG = "sv=2024&sig=SECRETSIGNATURE123&se=2030"

Route = Callable[["Recorded"], tuple[int, dict, bytes]]


class Recorded:
    def __init__(self, method: str, path: str, headers: dict[str, str]):
        self.method = method
        self.path = path
        self.headers = headers


class FakeServer:
    """A loopback HTTP server answering from a route table and recording every request."""

    def __init__(self, host: str = "127.0.0.1"):
        self.routes: dict[str, Route] = {}
        self.requests: list[Recorded] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def _serve(self):
                rec = Recorded(self.command, self.path, dict(self.headers))
                fake.requests.append(rec)
                route = fake.routes.get(self.path) or fake.routes.get(self.path.split("?")[0])
                status, headers, body = route(rec) if route else (404, {}, b"{}")
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = _serve

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.base = f"http://{host}:{self.port}"
        self.thread = threading.Thread(
            target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )
        self.thread.start()

    def json(self, path: str, payload, status: int = 200):
        body = json.dumps(payload).encode()
        self.routes[path] = lambda rec: (status, {"Content-Type": "application/json"}, body)

    def raw(self, path: str, body: bytes, status: int = 200, headers: dict | None = None):
        self.routes[path] = lambda rec: (status, headers or {}, body)

    def redirect(self, path: str, location: str, status: int = 302):
        self.routes[path] = lambda rec: (status, {"Location": location}, b"")

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def api():
    server = FakeServer("127.0.0.1")
    yield server
    server.close()


@pytest.fixture
def storage():
    # A different origin from the API (other host name and port), as GitHub's log storage is.
    server = FakeServer("localhost")
    yield server
    server.close()


@pytest.fixture(autouse=True)
def env(monkeypatch, api):
    for name in list(__import__("os").environ):
        if name.startswith("CI_READER_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("CI_READER_REPO", REPO)
    monkeypatch.setenv("GITHUB_API_URL", api.base)
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    monkeypatch.setenv("CI_READER_REDIRECT_HOSTS", "localhost")


def call(name: str, arguments=None) -> tuple[str, bool]:
    reply = cr.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": {} if arguments is None else arguments},
        }
    )
    result = reply["result"]
    return result["content"][0]["text"], bool(result.get("isError"))


def rpc(method: str, params=None, msg_id=1):
    message = {"jsonrpc": "2.0", "method": method}
    if msg_id is not None:
        message["id"] = msg_id
    if params is not None:
        message["params"] = params
    return cr.handle(message)


RUN = {
    "id": 4242,
    "name": "Tests",
    "run_number": 77,
    "run_attempt": 1,
    "event": "push",
    "head_branch": "main",
    "head_sha": "abc123",
    "status": "completed",
    "conclusion": "failure",
    "created_at": "2026-10-01T10:00:00Z",
    "updated_at": "2026-10-01T10:20:00Z",
    "html_url": "https://github.com/octo/demo-repo/actions/runs/4242",
}
JOBS = {
    "jobs": [
        {
            "id": 11,
            "name": "Offline suite",
            "status": "completed",
            "conclusion": "success",
            "started_at": "a",
            "completed_at": "b",
            "steps": [{"name": "Run Offline Tests", "conclusion": "success"}],
        },
        {
            "id": 12,
            "name": "Browser suite",
            "status": "completed",
            "conclusion": "failure",
            "started_at": "c",
            "completed_at": "d",
            "steps": [
                {"name": "Checkout", "conclusion": "success"},
                {"name": "Run Smoke Tests", "conclusion": "failure"},
                {"name": "Slow thing", "conclusion": "timed_out"},
                {"name": "Upload", "conclusion": "cancelled"},
                {"name": "Optional", "conclusion": "skipped"},
            ],
        },
    ]
}


def runs_path(suffix: str = "") -> str:
    return f"/repos/{REPO}/actions/runs{suffix}"


def setup_run(api: FakeServer):
    api.json(runs_path(), {"total_count": 1, "workflow_runs": [RUN]})
    api.json(runs_path("/4242"), RUN)
    api.json(runs_path("/4242/jobs"), JOBS)


def zip_bytes(files: dict[str, bytes | str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buf.getvalue()


def setup_artifact(api: FakeServer, storage: FakeServer, archive: bytes | None, *, expired=False):
    artifacts = []
    if archive is not None:
        artifacts.append(
            {
                "id": 9,
                "name": "test-reports",
                "expired": expired,
                "archive_download_url": f"{api.base}/repos/{REPO}/actions/artifacts/9/zip",
            }
        )
        api.redirect(f"/repos/{REPO}/actions/artifacts/9/zip", f"{storage.base}/blob/9.zip?{SIG}")
        storage.raw("/blob/9.zip", archive)
    api.json(runs_path("/4242/artifacts"), {"total_count": len(artifacts), "artifacts": artifacts})


JUNIT_A = """<?xml version="1.0"?>
<testsuites><testsuite name="a" tests="3">
  <testcase classname="tests.test_a" name="test_ok" time="0.1"/>
  <testcase classname="tests.test_a" name="test_bad" time="0.2">
    <failure message="{msg}">{stack}</failure>
  </testcase>
  <testcase classname="tests.test_a" name="test_skip" time="0"><skipped/></testcase>
</testsuite></testsuites>"""
JUNIT_B = """<?xml version="1.0"?>
<testsuite name="b" tests="2">
  <testcase classname="tests.test_b" name="test_boom" time="1.5">
    <error message="KeyError">Traceback\nline</error>
  </testcase>
  <testcase classname="tests.test_b" name="test_fine" time="0.3"/>
</testsuite>"""


def junit_a(msg: str = "assert 1 == 2", lines: int = 3) -> str:
    stack = "\n".join(f"stack line {i}" for i in range(1, lines + 1))
    return JUNIT_A.format(msg=msg, stack=stack)


# ------------------------------------------------------------------ protocol


def test_initialize_echoes_a_supported_version_and_falls_back_for_unknown():
    for version in cr.PROTOCOL_VERSIONS:
        result = rpc("initialize", {"protocolVersion": version})["result"]
        assert result["protocolVersion"] == version
        assert "tools" in result["capabilities"]
        assert result["serverInfo"]["name"]
    fallback = rpc("initialize", {"protocolVersion": "1999-01-01"})["result"]
    assert fallback["protocolVersion"] == cr.PROTOCOL_VERSIONS[0]
    assert len(cr.PROTOCOL_VERSIONS) >= 3


def test_tools_list_has_five_tools_with_schemas():
    tools = rpc("tools/list")["result"]["tools"]
    assert len(tools) == 5
    for tool in tools:
        assert tool["inputSchema"]["type"] == "object"
        assert tool["description"]


def test_ping():
    assert rpc("ping")["result"] == {}


def test_notification_gets_no_reply():
    assert rpc("notifications/initialized", msg_id=None) is None


def test_garbage_line_unknown_method_and_missing_tool_name():
    out = io.StringIO()
    cr.serve(io.StringIO('this is not json\n\n{"jsonrpc":"2.0","id":3,"method":"nope"}\n'), out)
    replies = [json.loads(line) for line in out.getvalue().splitlines()]
    assert replies[0]["error"]["code"] == -32700
    assert replies[1]["error"]["code"] == -32601
    assert len(replies) == 2  # the blank line was ignored
    assert rpc("tools/call", {"arguments": {}})["error"]["code"] == -32602
    assert cr.handle({"id": 1, "method": "ping"})["error"]["code"] == -32600
    assert cr.handle([1, 2])["error"]["code"] == -32600


def test_unknown_tool_and_non_object_arguments_are_error_results():
    text, err = call("delete_everything")
    assert err and "Unknown tool" in text
    reply = cr.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "recent_runs", "arguments": [1]},
        }
    )
    assert reply["result"]["isError"] is True


def test_a_crashing_handler_is_an_error_result_and_the_server_keeps_going(monkeypatch, capsys):
    def boom(cfg, args):
        raise ZeroDivisionError("nope")

    monkeypatch.setitem(cr.TOOLS["run_status"], "handler", boom)
    out = io.StringIO()
    lines = [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "run_status"}},
        {"jsonrpc": "2.0", "id": 2, "method": "ping"},
    ]
    cr.serve(io.StringIO("".join(json.dumps(m) + "\n" for m in lines)), out)
    first, second = (json.loads(line) for line in out.getvalue().splitlines())
    assert first["result"]["isError"] is True
    assert "ZeroDivisionError" in first["result"]["content"][0]["text"]
    assert second["result"] == {}
    assert "ZeroDivisionError" in capsys.readouterr().err


def test_real_subprocess_speaks_only_json_on_stdout(tmp_path):
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "x"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "ci_reader.py")],
        input="".join(json.dumps(m) + "\n" for m in messages),
        capture_output=True,
        text=True,
        timeout=30,
        env={"PATH": "", "CI_READER_REPO": ""},  # invalid config: warns on stderr, still serves
    )
    lines = proc.stdout.splitlines()
    replies = [json.loads(line) for line in lines]
    assert [r["id"] for r in replies] == [1, 2]
    assert len(replies[1]["result"]["tools"]) == 5
    assert "CI_READER_REPO" in proc.stderr


# ------------------------------------------------------------------ recent runs


def test_recent_runs_passes_filters_and_returns_summary_fields(api):
    api.json(runs_path(), {"total_count": 31, "workflow_runs": [RUN]})
    text, err = call("recent_runs", {"branch": "feat/x", "status": "failure", "limit": 5})
    assert not err, text
    query = api.requests[-1].path
    assert "branch=feat%2Fx" in query and "status=failure" in query and "per_page=5" in query
    payload = json.loads(text.split("\n", 1)[1]) if text.startswith("[") else json.loads(text)
    assert payload["total_count"] == 31
    run = payload["runs"][0]
    for key in ("id", "name", "run_number", "attempt", "event", "branch", "sha", "status"):
        assert key in run
    assert run["conclusion"] == "failure" and run["url"].endswith("/4242")


def test_recent_runs_uses_the_per_workflow_endpoint(api):
    api.json(
        f"/repos/{REPO}/actions/workflows/tests.yml/runs", {"total_count": 0, "workflow_runs": []}
    )
    text, err = call("recent_runs", {"workflow": "tests.yml"})
    assert not err, text
    assert api.requests[-1].path.startswith(f"/repos/{REPO}/actions/workflows/tests.yml/runs?")


# ---------------------------------------------------------------- run selection


def test_latest_resolves_through_the_newest_run_on_the_branch(api):
    setup_run(api)
    text, err = call("run_status", {"run": "latest", "branch": "release/1"})
    assert not err, text
    assert "branch=release%2F1" in api.requests[0].path and "per_page=1" in api.requests[0].path
    assert '"id": 4242' in text


def test_latest_on_a_branch_without_runs_is_not_found(api):
    api.json(runs_path(), {"total_count": 0, "workflow_runs": []})
    text, err = call("run_status", {"run": "latest", "branch": "lonely"})
    assert err and "Not found" in text and "lonely" in text


def test_latest_failed_filters_on_the_conclusion(api):
    setup_run(api)
    text, err = call("run_status", {"run": "latest failed"})
    assert not err, text
    assert "status=failure" in api.requests[0].path


def test_run_status_lists_jobs_and_only_bad_steps(api):
    setup_run(api)
    text, err = call("run_status", {"run": 4242})
    assert not err, text
    payload = json.loads(text[text.index("{") :])
    jobs = {j["name"]: j for j in payload["jobs"]}
    assert set(jobs) == {"Offline suite", "Browser suite"}
    assert jobs["Offline suite"]["failed_steps"] == []
    assert jobs["Browser suite"]["failed_steps"] == ["Run Smoke Tests", "Slow thing", "Upload"]


# ---------------------------------------------------------------------- job log


LOG = "\n".join(f"line {i}" for i in range(1, 101)) + f"\nauth {TOKEN} url https://x/y?{SIG}\n"


def setup_log(api: FakeServer, storage: FakeServer, text: str = LOG, job: int = 12):
    api.redirect(f"/repos/{REPO}/actions/jobs/{job}/logs", f"{storage.base}/logs/{job}.txt?{SIG}")
    storage.raw(f"/logs/{job}.txt", text.encode())


def test_job_log_tail_follows_an_allowed_redirect_and_masks_secrets(api, storage, capsys):
    setup_log(api, storage)
    text, err = call("job_log", {"job_id": 12, "tail_lines": 5})
    assert not err, text
    assert text.startswith(cr.UNTRUSTED)
    assert "last 5 of 101 lines" in text
    assert "line 100" in text and "line 95" not in text
    assert TOKEN not in text and "SECRETSIGNATURE123" not in text
    assert "sig=***" in text
    assert TOKEN not in capsys.readouterr().err


def test_storage_never_gets_the_token_but_a_same_origin_redirect_does(api, storage):
    setup_log(api, storage)
    call("job_log", {"job_id": 12})
    assert storage.requests and all("Authorization" not in r.headers for r in storage.requests)

    api.redirect(f"/repos/{REPO}/actions/jobs/13/logs", f"{api.base}/moved/13")
    api.raw("/moved/13", b"hello\n")
    text, err = call("job_log", {"job_id": 13})
    assert not err, text
    moved = [r for r in api.requests if r.path == "/moved/13"]
    assert moved and moved[0].headers.get("Authorization") == f"Bearer {TOKEN}"


def test_job_log_search_by_job_name(api, storage):
    setup_run(api)
    lines = [f"line {i}" for i in range(1, 41)]
    lines[9] = "ERROR first"
    lines[11] = "error second"
    lines[29] = "Error third"
    setup_log(api, storage, "\n".join(lines))
    text, err = call(
        "job_log", {"run": 4242, "job": "Browser suite", "grep": "error", "context": 1}
    )
    assert not err, text
    out = text.splitlines()
    assert "3 matching line(s) out of 40" in text
    assert "10: ERROR first" in out and "11- line 11" in out and "12: error second" in out
    assert out.count("--") == 1  # the first two windows merged, the third is separate
    assert "30: Error third" in out


def test_invalid_regex_is_searched_literally_and_no_hits_say_so(api, storage):
    setup_log(api, storage, "a (b\nc\n")
    text, err = call("job_log", {"job_id": 12, "grep": "(b"})
    assert not err and "1: a (b" in text.splitlines()
    text, _ = call("job_log", {"job_id": 12, "grep": "zzz"})
    assert "0 matching line(s)" in text and "No matches." in text


def test_search_output_stops_at_the_line_cap(api, storage):
    setup_log(api, storage, "\n".join("hit" for _ in range(1000)))
    text, err = call("job_log", {"job_id": 12, "grep": "hit", "context": 0})
    assert not err
    assert "stopped at 400 lines" in text
    assert len([line for line in text.splitlines() if line.endswith(": hit")]) == 400


def test_unknown_job_name_is_not_found_naming_both(api):
    setup_run(api)
    text, err = call("job_log", {"run": 4242, "job": "Ghost"})
    assert err and "Ghost" in text and "4242" in text


def test_log_over_the_body_cap_says_it_is_not_the_real_end(api, storage, monkeypatch):
    monkeypatch.setattr(cr, "LOG_CAP", 100)
    setup_log(api, storage, "x" * 500)
    text, err = call("job_log", {"job_id": 12})
    assert not err and "NOT the real end" in text


# ------------------------------------------------------------------- redirects


def test_redirect_off_the_allow_list_is_refused(api):
    api.redirect(f"/repos/{REPO}/actions/jobs/12/logs", "https://evil.example.net/steal")
    text, err = call("job_log", {"job_id": 12})
    assert err and "evil.example.net" in text


def test_lookalike_suffix_is_not_allowed():
    cfg = cr.load_config()
    with pytest.raises(cr.ToolError):
        cr.check_redirect(cfg, cfg.api + "/x", "https://evillocalhost/y", "GET", 302)
    assert cr.check_redirect(cfg, cfg.api + "/x", "http://localhost:1/y", "GET", 302)


def test_default_allow_list_matches_storage_suffixes_on_https_only(monkeypatch):
    monkeypatch.delenv("CI_READER_REDIRECT_HOSTS")
    cfg = cr.load_config()
    ok = "https://pipelines.actions.githubusercontent.com/a?sig=1"
    assert cr.check_redirect(cfg, cfg.api, ok, "GET", 302) == ok
    assert cr.check_redirect(cfg, cfg.api, "https://acct.blob.core.windows.net/c", "GET", 302)
    with pytest.raises(cr.ToolError):
        cr.check_redirect(cfg, cfg.api, "http://acct.blob.core.windows.net/c", "GET", 302)


def test_method_changing_redirect_is_refused():
    cfg = cr.load_config()
    with pytest.raises(cr.ToolError, match="method"):
        cr.check_redirect(cfg, cfg.api + "/a", cfg.api + "/b", "POST", 303)
    assert cr.method_after_redirect("GET", 303) == "GET"
    assert cr.method_after_redirect("POST", 307) == "POST"


def test_every_request_is_a_get(api, storage):
    setup_run(api)
    setup_log(api, storage)
    setup_artifact(api, storage, zip_bytes({"junit.xml": junit_a(), "run-trust.txt": "OK"}))
    api.json(runs_path(), {"total_count": 1, "workflow_runs": [RUN]})
    for name, args in [
        ("recent_runs", {}),
        ("run_status", {}),
        ("job_log", {"run": 4242, "job": "Browser suite"}),
        ("failed_tests", {"run": 4242}),
        ("run_verdict", {"run": 4242}),
    ]:
        _, err = call(name, args)
        assert not err, name
    methods = {r.method for r in api.requests + storage.requests}
    assert methods == {"GET"}
    assert len(api.requests) >= 8


# ---------------------------------------------------------------- failed tests


def test_failed_tests_aggregate_two_files_and_truncate(api, storage):
    setup_run(api)
    archive = zip_bytes(
        {"junit-offline.xml": junit_a("m" * 900, lines=30), "sub/junit-browser.xml": JUNIT_B}
    )
    setup_artifact(api, storage, archive)
    text, err = call("failed_tests", {"run": 4242})
    assert not err, text
    assert text.startswith(cr.UNTRUSTED)
    assert "5 tests, 1 failed, 1 errors, 1 skipped" in text
    assert "[failure] tests.test_a::test_bad" in text and "[error] tests.test_b::test_boom" in text
    assert "m" * 600 in text and "m" * 601 not in text
    assert "stack line 15" in text and "stack line 16" not in text
    text, _ = call("failed_tests", {"run": 4242, "limit": 1})
    assert "Showing 1 of 2" in text and "test_boom" not in text


def test_failed_tests_missing_expired_and_no_xml(api, storage):
    setup_run(api)
    setup_artifact(api, storage, None)
    text, err = call("failed_tests", {"run": 4242})
    assert not err and "no 'test-reports' artifact" in text and "retention" in text

    setup_artifact(api, storage, zip_bytes({"a.txt": "x"}), expired=True)
    text, err = call("failed_tests", {"run": 4242})
    assert not err and "expired" in text

    setup_artifact(api, storage, zip_bytes({"a.txt": "x", "empty.xml": "<testsuite/>"}))
    text, err = call("failed_tests", {"run": 4242})
    assert not err and "no JUnit results" in text and "--junitxml" in text


def test_broken_xml_is_listed_as_unreadable(api, storage):
    setup_run(api)
    setup_artifact(api, storage, zip_bytes({"good.xml": JUNIT_B, "broken.xml": "<testsuite"}))
    text, err = call("failed_tests", {"run": 4242})
    assert not err and "Unreadable XML: broken.xml" in text and "test_boom" in text


def test_non_zip_and_oversized_artifacts(api, storage, monkeypatch):
    setup_run(api)
    setup_artifact(api, storage, b"definitely not a zip")
    text, err = call("failed_tests", {"run": 4242})
    assert err and "not a zip" in text

    monkeypatch.setattr(cr, "MEMBER_CAP", 1000)
    setup_artifact(api, storage, zip_bytes({"big.xml": "x" * 5000, "ok.xml": JUNIT_B}))
    text, err = call("failed_tests", {"run": 4242})
    assert not err and "Skipped big.xml" in text and "test_boom" in text

    monkeypatch.setattr(cr, "ARTIFACT_CAP", 100)
    text, err = call("failed_tests", {"run": 4242})
    assert err and "larger than" in text


# ------------------------------------------------------------------ run verdict


def test_run_verdict_prints_found_files_and_did_not_run_lines(api, storage):
    setup_run(api)
    archive = zip_bytes(
        {
            "run-trust-browser.txt": "VERDICT=PASS ENFORCEMENT=fail",
            "verdicts/verdict-status.md": "# status\nall reached",
            "deep/er/verdict-status.md": "too deep, ignored",
        }
    )
    setup_artifact(api, storage, archive)
    text, err = call("run_verdict", {"run": 4242})
    assert not err, text
    assert text.startswith(cr.UNTRUSTED)
    assert "## run-trust-browser.txt\nVERDICT=PASS" in text
    assert "## verdicts/verdict-status.md\n# status" in text
    assert "too deep" not in text

    setup_artifact(api, storage, zip_bytes({"other.txt": "x"}))
    text, _ = call("run_verdict", {"run": 4242})
    assert text.count("did not run") == 2
    assert "## run-trust*.txt" in text and "## verdict-status.md" in text


def test_run_verdict_without_an_artifact_is_a_plain_message(api, storage):
    setup_run(api)
    setup_artifact(api, storage, None)
    text, err = call("run_verdict", {"run": 4242})
    assert not err and "no 'test-reports' artifact" in text


# ------------------------------------------------------------------------ caps


def test_output_over_the_character_cap_is_cut_with_a_notice(api, storage, monkeypatch):
    monkeypatch.setattr(cr, "OUTPUT_CAP", 300)
    setup_log(api, storage)
    text, err = call("job_log", {"job_id": 12})
    assert not err
    assert "[output cut at 300 of" in text
    assert len(text) < 400


# ---------------------------------------------------------------------- errors


@pytest.mark.parametrize(
    ("status", "expect"),
    [(404, "Not found"), (401, "Access refused"), (403, "GITHUB_TOKEN"), (500, "HTTP 500")],
)
def test_http_errors_map_to_readable_messages(api, status, expect):
    api.json(runs_path("/5"), {"message": "x"}, status=status)
    text, err = call("run_status", {"run": 5})
    assert err and expect in text
    assert "?" not in text.split("/actions/runs/5")[-1][:3]


def test_404_names_the_path_without_its_query(api):
    text, err = call("recent_runs", {"branch": "b"})
    assert err and f"/repos/{REPO}/actions/runs" in text and "per_page" not in text


def test_connection_refused_and_timeout(monkeypatch, api):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        dead = sock.getsockname()[1]
    monkeypatch.setenv("GITHUB_API_URL", f"http://127.0.0.1:{dead}")
    text, err = call("run_status", {"run": 5})
    assert err and "Could not reach 127.0.0.1" in text

    monkeypatch.setenv("GITHUB_API_URL", api.base)
    monkeypatch.setattr(cr, "TIMEOUT_S", 0.3)

    def slow(rec):
        time.sleep(1.5)
        return 200, {}, b"{}"

    api.routes[runs_path("/6")] = slow
    text, err = call("run_status", {"run": 6})
    assert err and "Timed out" in text


def test_non_json_where_json_was_expected(api):
    api.raw(runs_path("/7"), b"<html>oops</html>")
    text, err = call("run_status", {"run": 7})
    assert err and "Expected JSON" in text


# ------------------------------------------------------------------- validation


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("run_status", {"run": 0}),
        ("run_status", {"run": 10**12 + 1}),
        ("run_status", {"run": True}),
        ("run_status", {"run": "yesterday"}),
        ("recent_runs", {"limit": True}),
        ("recent_runs", {"limit": 101}),
        ("recent_runs", {"limit": 0}),
        ("recent_runs", {"branch": "a/../b"}),
        ("recent_runs", {"branch": "bad branch"}),
        ("recent_runs", {"branch": "x;rm"}),
        ("recent_runs", {"branch": "b" * 201}),
        ("recent_runs", {"workflow": "tests.json"}),
        ("recent_runs", {"workflow": "../tests.yml"}),
        ("recent_runs", {"status": "exploded"}),
        ("job_log", {"job_id": 12, "grep": "x" * 201}),
        ("job_log", {"job_id": 12, "tail_lines": 5001}),
        ("job_log", {"job_id": 12, "context": 21}),
        ("job_log", {"job_id": False}),
        ("job_log", {"run": 4242}),
        ("failed_tests", {"limit": 201}),
    ],
)
def test_invalid_arguments_never_reach_the_network(api, tool, args):
    text, err = call(tool, args)
    assert err, text
    assert api.requests == []


@pytest.mark.parametrize(
    ("name", "value", "expect"),
    [
        ("CI_READER_REPO", "", "CI_READER_REPO"),
        ("CI_READER_REPO", "no-slash", "owner/name"),
        ("CI_READER_REPO", "own er/x", "owner/name"),
        ("CI_READER_REPO", "o/..", "owner/name"),
        ("GITHUB_API_URL", "http://api.example.com", "https"),
        ("CI_READER_ARTIFACT", "bad/name", "CI_READER_ARTIFACT"),
    ],
)
def test_invalid_configuration_is_an_error_result(monkeypatch, api, name, value, expect):
    monkeypatch.setenv(name, value)
    text, err = call("recent_runs")
    assert err and expect in text
    assert api.requests == []


def test_defaults_apply_when_only_the_repo_is_set(monkeypatch):
    for name in ("GITHUB_API_URL", "GITHUB_TOKEN", "CI_READER_REDIRECT_HOSTS"):
        monkeypatch.delenv(name)
    cfg = cr.load_config()
    assert cfg.api == "https://api.github.com" and cfg.token is None
    assert cfg.artifact == "test-reports"
    assert cfg.redirect_hosts == cr.DEFAULT_REDIRECT_HOSTS
    assert cfg.verdict_files == cr.DEFAULT_VERDICT_FILES
    monkeypatch.setenv("GITHUB_API_URL", "https://ghe.example.com/api/v3/")
    assert cr.load_config().api == "https://ghe.example.com/api/v3"


# -------------------------------------------------------------------- redaction


def test_token_and_signature_in_error_text_are_masked_on_stderr_too(api, monkeypatch, capsys):
    def leaky(cfg, args):
        raise RuntimeError(f"failed with {TOKEN} at https://h/p?{SIG}")

    monkeypatch.setitem(cr.TOOLS["run_status"], "handler", leaky)
    text, err = call("run_status")
    assert err and TOKEN not in text
    stderr = capsys.readouterr().err
    assert stderr and TOKEN not in stderr and "SECRETSIGNATURE123" not in stderr
    assert "sig=***" in stderr


def test_ci_content_results_start_with_the_untrusted_line(api, storage):
    setup_run(api)
    setup_log(api, storage)
    setup_artifact(api, storage, zip_bytes({"junit.xml": junit_a(), "run-trust.txt": "OK"}))
    for name, args in [
        ("recent_runs", {}),
        ("run_status", {"run": 4242}),
        ("job_log", {"job_id": 12}),
        ("job_log", {"job_id": 12, "grep": "line"}),
        ("failed_tests", {"run": 4242}),
        ("run_verdict", {"run": 4242}),
    ]:
        text, err = call(name, args)
        assert not err and text.startswith(cr.UNTRUSTED), name


# ----------------------------------------------------------------- registration


def test_the_repository_registers_this_server():
    config = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))
    entries = [
        e for e in config["mcpServers"].values() if any("ci_reader" in a for a in e.get("args", []))
    ]
    assert len(entries) == 1
    assert (ROOT / entries[0]["args"][-1]).resolve() == (ROOT / "tools" / "ci_reader.py").resolve()
