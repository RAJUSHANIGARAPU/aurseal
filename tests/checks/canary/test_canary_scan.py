"""The canary log scan offline: matching, verdicts, three backends, CLI and output hygiene.

Loki is a real HTTP server on localhost in a thread; CloudWatch is a fake ``aws`` runner.
No network and no AWS account are touched.
"""

from __future__ import annotations

import json
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from checks.canary import CanaryRun, Source, Target, Verdict, load_artifacts, scan_targets
from checks.canary.__main__ import main
from checks.canary.backends import (
    BackendError,
    BackendTimeout,
    CloudWatchBackend,
    FilesBackend,
    LokiBackend,
    Row,
    SearchResult,
)
from checks.canary.canaries import Canary, synthetic_iban
from checks.canary.matching import Matcher
from checks.canary.scan import scan_target
from checks.canary.selftest import run_selftest
from checks.canary.targets import parse_targets

T0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=1)
T1 = T0 + timedelta(minutes=10)
SECRET_LOG_TEXT = "password=hunter2 card=0000 note=do-not-print-me"


@pytest.fixture(autouse=True)
def _no_ext_config(monkeypatch):
    monkeypatch.delenv("EXT_CONFIG_PATH", raising=False)


def _run(tag="r1") -> CanaryRun:
    return CanaryRun(tag, started_at=T0)


class _FakeBackend:
    name = "fake"

    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, []

    def search(self, sources, pattern, start, end, **kw):
        self.calls.append((list(sources), pattern, start, end, kw))
        if self.error:
            raise self.error
        return self.result


def _artifact(run: CanaryRun):
    return run.finish(T1)


# ------------------------------------------------------------------ minting


class TestCanaryRun:
    def test_values_embed_the_tag_and_are_recorded(self):
        run = _run("run-7")
        assert run.name() == "canary-run-7"
        assert run.email() == "qa+run-7@example.test"
        assert run.name(2) == "canary-run-7-2"
        kinds = {(c.kind, c.value) for c in run.artifact().canaries}
        assert ("name", "canary-run-7") in kinds and ("email", "qa+run-7@example.test") in kinds

    def test_iban_is_valid_mod97_and_run_unique(self):
        a, b = synthetic_iban("run-a"), synthetic_iban("run-b")
        assert a != b and a == synthetic_iban("run-a")
        rearranged = a[4:] + a[:4]
        assert int("".join(str(int(c, 36)) for c in rearranged)) % 97 == 1

    def test_default_tag_comes_from_run_id_and_worker_prefix(self):
        env = {"TEST_RUN_ID": "Gh-42-1", "TEST_WORKER_PREFIX": "w2"}
        assert CanaryRun(environ=env).name() == "canary-w2-gh-42-1"

    def test_artifact_roundtrip_and_merge(self, tmp_path):
        one, two = _run("r"), _run("r")
        one.name(), two.email()
        one.finish(T1)
        one.save(tmp_path / "a.json")
        two.finish(T1 + timedelta(minutes=1))
        two.save(tmp_path / "b.json")
        merged = load_artifacts([tmp_path / "a.json", tmp_path / "b.json"])
        assert {c.kind for c in merged.canaries} == {"name", "email"}
        assert merged.ended_at == T1 + timedelta(minutes=1)

    def test_artifacts_of_different_runs_do_not_merge(self, tmp_path):
        _run("a").save(tmp_path / "a.json")
        _run("b").save(tmp_path / "b.json")
        with pytest.raises(ValueError, match="different runs"):
            load_artifacts([tmp_path / "a.json", tmp_path / "b.json"])


# ------------------------------------------------------------------ matching


class TestMatcher:
    def test_short_canary_is_not_counted_inside_a_longer_one(self):
        m = Matcher([Canary("name", "canary-r1")])
        assert m.kinds_in("user=canary-r1 ok") == ["name"]
        assert m.kinds_in("user=canary-r10 ok") == []
        assert m.kinds_in("user=canary-r1-2 ok") == []
        assert m.kinds_in("xcanary-r1") == []
        assert m.kinds_in("user: canary-r1.") == ["name"]

    def test_email_classification_and_case_insensitive(self):
        m = Matcher([Canary("email", "qa+r1@example.test"), Canary("name", "canary-r1")])
        assert m.kinds_in('{"to":"QA+R1@Example.Test"}') == ["email"]
        assert sorted(m.kinds_in("canary-r1 mailed qa+r1@example.test")) == ["email", "name"]

    def test_iban_matches_compact_and_grouped(self):
        iban = synthetic_iban("r1")
        m = Matcher([Canary("account", iban)])
        grouped = " ".join(iban[i : i + 4] for i in range(0, len(iban), 4))
        assert m.kinds_in(f"iban={iban}") == ["account"]
        assert m.kinds_in(f"iban={grouped}") == ["account"]
        assert m.kinds_in(f"iban={iban}99") == []


# ------------------------------------------------------------------ verdicts


class TestVerdicts:
    def _scan(self, backend, run=None, sources=None, **kw):
        sources = (Source("s"),) if sources is None else sources
        run = run or _run()
        run.email()
        return scan_target(Target("t", tuple(sources)), _artifact(run), backend, **kw)

    def test_hit_is_still_leaking_with_counts_per_source_and_kind(self):
        rows = [
            Row("s", "to=qa+r1@example.test"),
            Row("s", "noise"),
            Row("s", "qa+r1@example.test"),
        ]
        result = self._scan(_FakeBackend(SearchResult(rows)))
        assert result.verdict is Verdict.STILL_LEAKING
        assert result.hits == {"s": {"email": 2}}

    def test_no_hit_complete_search_is_cleared(self):
        result = self._scan(_FakeBackend(SearchResult([Row("s", "noise")])))
        assert result.verdict is Verdict.CLEARED

    def test_truncated_without_hit_is_not_assessed(self):
        result = self._scan(_FakeBackend(SearchResult([Row("s", "noise")], truncated=True)))
        assert result.verdict is Verdict.NOT_ASSESSED and result.truncated

    def test_truncated_with_hit_stays_still_leaking(self):
        rows = [Row("s", "qa+r1@example.test")]
        result = self._scan(_FakeBackend(SearchResult(rows, truncated=True)))
        assert result.verdict is Verdict.STILL_LEAKING and "incomplete" in result.reason

    def test_missing_source_is_not_assessed(self):
        result = self._scan(_FakeBackend(SearchResult([], missing=["s"])))
        assert result.verdict is Verdict.NOT_ASSESSED and result.missing == ["s"]

    def test_backend_error_is_not_assessed(self):
        result = self._scan(_FakeBackend(error=BackendError("Loki answered HTTP 500")))
        assert result.verdict is Verdict.NOT_ASSESSED and "HTTP 500" in result.reason

    def test_target_without_source_is_not_assessed(self):
        result = self._scan(_FakeBackend(SearchResult()), sources=())
        assert result.verdict is Verdict.NOT_ASSESSED

    def test_window_is_run_start_to_end_plus_grace(self):
        backend = _FakeBackend(SearchResult())
        self._scan(backend, grace=timedelta(minutes=5))
        _, _, start, end, _ = backend.calls[0]
        assert start == T0 and end == T1 + timedelta(minutes=5)

    def test_marker_splits_one_shared_source(self):
        rows = [
            Row("shared", "[orders] qa+r1@example.test"),
            Row("shared", "[billing] qa+r1@example.test"),
            Row("shared", "[orders] healthcheck qa+r1@example.test"),
        ]
        backend = _FakeBackend(SearchResult(rows))
        run = _run()
        run.email()
        artifact = _artifact(run)
        orders = Target("orders", (Source("shared", include="[orders]", exclude="healthcheck"),))
        billing = Target("billing", (Source("shared", include="[checkout]"),))
        (o, b) = scan_targets([orders, billing], artifact, backend)
        assert o.hits == {"shared": {"email": 1}}
        assert b.verdict is Verdict.CLEARED


class TestTargetMap:
    def test_parse_string_and_mapping_sources(self):
        (t,) = parse_targets(
            {"targets": {"x": {"sources": ["a", {"source": "b", "include": "m"}]}}}
        )
        assert t.sources == (Source("a"), Source("b", include="m"))

    def test_rejects_bad_maps(self):
        with pytest.raises(ValueError):
            parse_targets({"targets": {}})
        with pytest.raises(ValueError, match="unknown keys"):
            parse_targets({"targets": {"x": {"sources": [{"source": "a", "inclde": "m"}]}}})


# ------------------------------------------------------------------ files backend


class TestFilesBackend:
    def test_end_to_end_leak_clear_and_missing(self, tmp_path):
        run = _run()
        email, name = run.email(), run.name()
        (tmp_path / "svc-a").mkdir()
        (tmp_path / "svc-a" / "app.log").write_text(f"{SECRET_LOG_TEXT} {email}\n")
        (tmp_path / "svc-b.log").write_text(f"clean line\nlonger {name}-x\n")
        targets = [
            Target("a", (Source("svc-a"),)),
            Target("b", (Source("svc-b.log"),)),
            Target("c", (Source("nope.log"),)),
            Target("d", (Source("svc-*"),)),
        ]
        results = {
            r.target: r
            for r in scan_targets(targets, _artifact(run), FilesBackend(tmp_path), now=T1)
        }
        assert results["a"].verdict is Verdict.STILL_LEAKING
        assert results["b"].verdict is Verdict.CLEARED
        assert results["c"].verdict is Verdict.NOT_ASSESSED
        assert results["d"].verdict is Verdict.STILL_LEAKING

    def test_row_limit_marks_truncated(self, tmp_path):
        (tmp_path / "x.log").write_text("canary-r1-a\n" * 5)
        got = FilesBackend(tmp_path).search(["x.log"], "(?i)canary", T0, T1, limit=3)
        assert got.truncated and len(got.rows) == 3

    def test_file_older_than_the_run_is_skipped(self, tmp_path):
        log = tmp_path / "old.log"
        log.write_text("canary-r1\n")
        future = T1 + timedelta(days=1)
        got = FilesBackend(tmp_path).search(["old.log"], "canary", future, future)
        assert got.rows == [] and got.missing == []

    def test_scan_deadline_stops_the_scan(self, tmp_path):
        (tmp_path / "x.log").write_text("noise\n" * 10)
        with pytest.raises(BackendTimeout):
            FilesBackend(tmp_path).search(["x.log"], "canary", T0, T1, timeout=-1)


# ------------------------------------------------------------------ loki backend


class _LokiHandler(BaseHTTPRequestHandler):
    seen: list[dict] = []
    lines: list[str] = []
    status = 200
    delay = 0.0

    def do_GET(self):  # noqa: N802
        parts = urlsplit(self.path)
        self.seen.append(
            {
                "path": parts.path,
                "q": parse_qs(parts.query),
                "auth": self.headers.get("Authorization"),
            }
        )
        if self.delay:
            threading.Event().wait(self.delay)
        body = json.dumps(
            {"data": {"result": [{"stream": {}, "values": [["1", t] for t in self.lines]}]}}
        ).encode()
        try:
            self.send_response(self.status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            pass

    def log_message(self, *args):
        pass


@pytest.fixture
def loki():
    handler = type("H", (_LokiHandler,), {"seen": [], "lines": [], "status": 200, "delay": 0.0})
    server = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield handler, f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


class TestLokiBackend:
    def test_query_shape_token_header_and_hit(self, loki):
        handler, url = loki
        run = _run()
        email = run.email()
        handler.lines = [f"to {email} {SECRET_LOG_TEXT}"]
        backend = LokiBackend(url, token_env="LOKI_TOKEN", environ={"LOKI_TOKEN": "tok-123"})
        (result,) = scan_targets([Target("t", (Source('{job="svc"}'),))], _artifact(run), backend)
        assert result.verdict is Verdict.STILL_LEAKING
        seen = handler.seen[0]
        assert seen["path"] == "/loki/api/v1/query_range"
        assert seen["q"]["query"][0].startswith('{job="svc"} |~ "')
        assert seen["auth"] == "Bearer tok-123"
        assert seen["q"]["direction"] == ["forward"]

    def test_default_selector_is_job_regex(self, loki):
        handler, url = loki
        LokiBackend(url).search([""], "(?i)x", T0, T1)
        assert handler.seen[0]["q"]["query"][0].startswith('{job=~".+"} |~ ')

    def test_limit_reached_is_truncated(self, loki):
        handler, url = loki
        handler.lines = ["a", "b"]
        got = LokiBackend(url).search(['{a="b"}'], "x", T0, T1, limit=2)
        assert got.truncated

    def test_http_error_is_a_backend_error_without_body(self, loki):
        handler, url = loki
        handler.status = 500
        with pytest.raises(BackendError, match="HTTP 500"):
            LokiBackend(url).search(['{a="b"}'], "x", T0, T1)

    def test_slow_server_times_out(self, loki):
        handler, url = loki
        handler.delay = 2.0
        with pytest.raises(BackendTimeout):
            LokiBackend(url).search(['{a="b"}'], "x", T0, T1, timeout=0.3)

    def test_missing_token_variable_fails_closed(self, loki):
        _, url = loki
        with pytest.raises(BackendError, match="NOPE"):
            LokiBackend(url, token_env="NOPE", environ={}).search(['{a="b"}'], "x", T0, T1)

    def test_credentials_in_the_url_are_rejected(self):
        with pytest.raises(ValueError):
            LokiBackend("http://user:pw@host:3100")


# ------------------------------------------------------------------ cloudwatch backend


class _FakeAws:
    """A fake ``aws`` runner: start-query, get-query-results (n polls), stop-query."""

    def __init__(self, rows=(), polls=0, status="Complete"):
        self.calls: list[list[str]] = []
        self.rows, self.polls, self.status = list(rows), polls, status
        self._left = polls

    def __call__(self, argv, timeout):
        self.calls.append(list(argv))
        op = argv[2]
        out: dict = {}
        if op == "start-query":
            out = {"queryId": f"q{sum(1 for c in self.calls if c[2] == 'start-query')}"}
        elif op == "get-query-results":
            if self._left > 0:
                self._left -= 1
                out = {"status": "Running", "results": []}
            else:
                results = [
                    [
                        {"field": "@log", "value": f"123456789012:{g}"},
                        {"field": "@message", "value": m},
                    ]
                    for g, m in self.rows
                ]
                out = {"status": self.status, "results": results}
        return subprocess.CompletedProcess(argv, 0, json.dumps(out), "")

    def ops(self, name):
        return [c for c in self.calls if c[2] == name]


def _cw(runner, **kw):
    clock = kw.pop("clock", None) or (lambda: 0.0)
    return CloudWatchBackend(runner=runner, sleep=lambda s: None, clock=clock, **kw)


class TestCloudWatchBackend:
    def test_hit_attributed_to_group_and_account_id_dropped(self):
        run = _run()
        email = run.email()
        aws = _FakeAws(rows=[("/aws/app", f"mail {email}")], polls=2)
        (result,) = scan_targets(
            [Target("t", (Source("/aws/app"),))], _artifact(run), _cw(aws, region="eu-west-1")
        )
        assert result.verdict is Verdict.STILL_LEAKING
        assert result.hits == {"/aws/app": {"email": 1}}
        assert "123456789012" not in json.dumps(result.to_json())
        assert "--region" in aws.calls[0] and "eu-west-1" in aws.calls[0]

    def test_groups_are_batched_at_most_50(self):
        aws = _FakeAws()
        groups = [f"/g/{i}" for i in range(120)]
        _cw(aws).search(groups, "(?i)x", T0, T1)
        starts = aws.ops("start-query")
        sizes = []
        for call in starts:
            i = call.index("--log-group-names") + 1
            j = call.index("--start-time")
            sizes.append(j - i)
        assert sizes == [50, 50, 20]

    def test_row_limit_is_truncated(self):
        aws = _FakeAws(rows=[("/g", "a"), ("/g", "b")])
        got = _cw(aws).search(["/g"], "x", T0, T1, limit=2)
        assert got.truncated
        assert aws.ops("start-query")[0][aws.ops("start-query")[0].index("--limit") + 1] == "2"

    def test_timeout_stops_the_query(self):
        ticks = iter(range(0, 1000, 5))
        aws = _FakeAws(polls=10**6)
        backend = _cw(aws, clock=lambda: float(next(ticks)))
        with pytest.raises(BackendTimeout):
            backend.search(["/g"], "x", T0, T1, timeout=12)
        (stop,) = aws.ops("stop-query")
        assert stop[stop.index("--query-id") + 1] == "q1"

    def test_failed_query_is_a_backend_error(self):
        with pytest.raises(BackendError, match="Failed"):
            _cw(_FakeAws(status="Failed")).search(["/g"], "x", T0, T1)

    def test_nonzero_exit_is_a_backend_error(self):
        def runner(argv, timeout):
            return subprocess.CompletedProcess(argv, 255, "", "AccessDenied: secret detail")

        with pytest.raises(BackendError) as info:
            _cw(runner).search(["/g"], "x", T0, T1)
        assert "secret detail" not in str(info.value)

    def test_missing_aws_cli_is_a_backend_error(self):
        def runner(argv, timeout):
            raise FileNotFoundError

        with pytest.raises(BackendError, match="not installed"):
            _cw(runner).search(["/g"], "x", T0, T1)

    def test_credentials_never_in_argv(self, monkeypatch):
        sentinel = "AKIA-SENTINEL-SECRET-VALUE"
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", sentinel)
        monkeypatch.setenv("AWS_SESSION_TOKEN", sentinel)
        aws = _FakeAws(polls=1)
        _cw(aws, profile="reporting").search(["/g"], "x", T0, T1)
        assert aws.calls and all(sentinel not in " ".join(c) for c in aws.calls)


# ------------------------------------------------------------------ positive control + CLI


class TestSelftestAndCli:
    def test_positive_control_reports_still_leaking(self):
        assert run_selftest().status == "pass"

    def test_selftest_fails_when_the_scanner_cannot_see_a_leak(self, monkeypatch):
        import checks.canary.selftest as st

        monkeypatch.setattr(st, "prefilter_pattern", lambda canaries: "(?!)", raising=False)
        monkeypatch.setattr("checks.canary.scan.prefilter_pattern", lambda c: "(?!)")
        assert run_selftest().status == "fail"

    def _setup(self, tmp_path, leak: bool):
        run = _run("cli")
        email = run.email()
        run.finish(datetime.now(timezone.utc))
        artifact = run.save(tmp_path / "run.json")
        logs = tmp_path / "logs"
        logs.mkdir()
        (logs / "svc.log").write_text(
            (f"{SECRET_LOG_TEXT} {email}\n" if leak else f"{SECRET_LOG_TEXT}\n"), encoding="utf-8"
        )
        targets = tmp_path / "targets.yml"
        targets.write_text(
            "targets:\n  svc:\n    sources: [svc.log]\n  gone:\n    sources: [gone.log]\n"
        )
        return artifact, targets, logs

    def _scan(self, tmp_path, leak, *extra):
        artifact, targets, logs = self._setup(tmp_path, leak)
        return main(
            [
                "scan",
                "--run-artifact",
                str(artifact),
                "--targets",
                str(targets),
                "--backend",
                "files",
                "--log-root",
                str(logs),
                *extra,
            ]
        )

    def test_exit_1_on_leak_and_output_has_no_log_lines(self, tmp_path, capsys):
        code = self._scan(tmp_path, True, "--out-dir", str(tmp_path / "out"))
        assert code == 1
        written = (tmp_path / "out" / "canary-scan.json").read_text()
        written += (tmp_path / "out" / "canary-scan.md").read_text()
        for text in (capsys.readouterr().out, written):
            assert "hunter2" not in text and "do-not-print-me" not in text
            assert "qa+cli@example.test" not in text
        assert '"STILL_LEAKING"' in written

    def test_exit_2_when_a_target_is_not_assessed(self, tmp_path, capsys):
        assert self._scan(tmp_path, False) == 2
        assert "NOT_ASSESSED" in capsys.readouterr().out

    def test_markdown_format(self, tmp_path, capsys):
        self._scan(tmp_path, True, "--format", "md")
        out = capsys.readouterr().out
        assert out.startswith("# Canary log scan") and "CLEARED covers only" in out

    def test_unreadable_artifact_is_exit_2(self, tmp_path, capsys):
        targets = tmp_path / "t.yml"
        targets.write_text("targets: {x: {sources: [a]}}\n")
        code = main(
            ["scan", "--run-artifact", str(tmp_path / "no.json"), "--targets", str(targets)]
        )
        assert code == 2

    def test_loki_without_url_is_exit_2(self, tmp_path):
        artifact, targets, _ = self._setup(tmp_path, True)
        code = main(
            [
                "scan",
                "--run-artifact",
                str(artifact),
                "--targets",
                str(targets),
                "--backend",
                "loki",
            ]
        )
        assert code == 2
