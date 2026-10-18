"""The load engine offline: profile rules, script rendering, summary parsing, judging.

The summary fixtures were recorded from real k6 v2.3.0 runs against the demo service
(``tests/checks/fixtures/k6-summary-*.json``). k6 itself is never started here.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from checks._sink import MemorySink
from checks.load import k6
from checks.load.profile import LoadProfile, ProfileError, Threshold
from checks.load.runner import K6Runner, record, run_load

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
SMOKE = Path(__file__).resolve().parents[3] / "checks" / "load" / "profiles" / "smoke.json"


def _profile(*thresholds: tuple[str, str]) -> LoadProfile:
    pairs = thresholds or (("http_req_duration", "p(95)<500"),)
    return LoadProfile(
        stages=(("1s", 2),),
        thresholds=tuple(Threshold(m, e) for m, e in pairs),
    )


class TestProfile:
    def test_a_profile_without_thresholds_is_refused(self):
        with pytest.raises(ProfileError, match="nothing to judge"):
            LoadProfile(stages=(("1s", 1),), thresholds=())

    def test_a_profile_without_load_is_refused(self):
        with pytest.raises(ProfileError):
            LoadProfile(stages=(("1s", 0),), thresholds=(Threshold("checks", "rate>0.9"),))

    @pytest.mark.parametrize(
        ("metric", "expr"),
        [("http_req_duration", "p95 < 500"), ("Bad-Metric", "rate<1"), ("checks", "rate<1;x")],
    )
    def test_malformed_thresholds_are_refused(self, metric, expr):
        with pytest.raises(ProfileError):
            Threshold(metric, expr)

    def test_bad_duration_is_refused(self):
        with pytest.raises(ProfileError):
            LoadProfile(stages=(("ten", 1),), thresholds=(Threshold("checks", "rate>0.9"),))

    def test_the_committed_smoke_profile_loads(self):
        profile = LoadProfile.from_json(SMOKE)
        assert profile.vus_max == 5
        assert profile.duration_s == 20.0
        assert {t.metric for t in profile.thresholds} == {
            "http_req_duration",
            "http_req_failed",
            "checks",
        }

    def test_round_trip(self):
        profile = LoadProfile.from_json(SMOKE)
        assert LoadProfile.from_dict(profile.to_dict()) == profile

    def test_unreadable_profile_file(self, tmp_path):
        bad = tmp_path / "p.json"
        bad.write_text("{nope", encoding="utf-8")
        with pytest.raises(ProfileError):
            LoadProfile.from_json(bad)


class TestRender:
    def test_stages_thresholds_and_trend_stats_are_in_the_options(self):
        script = k6.render_script(["https://svc.example/a"], LoadProfile.from_json(SMOKE))
        options = json.loads(re.search(r"options = (\{.*?\n\});", script, re.S).group(1))
        assert options["stages"][0] == {"duration": "5s", "target": 5}
        assert options["thresholds"]["http_req_duration"] == ["p(95)<500"]
        assert "p(99)" in options["summaryTrendStats"]
        assert "import http from 'k6/http';" in script
        assert "export default function ()" in script

    def test_a_route_with_quotes_cannot_break_out(self):
        nasty = "https://svc.example/a'];import x from 'evil';//\"]"
        script = k6.render_script([nasty], _profile())
        line = next(ln for ln in script.splitlines() if ln.startswith("const ROUTES = "))
        assert json.loads(line.removeprefix("const ROUTES = ").rstrip(";")) == [nasty]
        assert "import x from" not in script.replace(json.dumps([nasty]), "")


class TestParseSummary:
    def test_a_passing_run(self):
        m = k6.parse_summary(FIXTURES / "k6-summary-pass.json")
        assert m.requests > 0 and m.rps > 0
        assert m.p95_ms is not None and m.p99_ms is not None and m.p50_ms is not None
        assert m.error_rate == 0
        assert m.breached == []
        assert set(m.thresholds) >= {"http_req_duration: p(95)<500", "checks: rate>0.99"}

    def test_a_breached_run_names_the_threshold(self):
        m = k6.parse_summary(FIXTURES / "k6-summary-breach.json")
        assert m.breached == ["http_req_duration: p(95)<0.001"]

    @pytest.mark.parametrize("content", ["", "{}", '{"metrics": {}}', "[1]"])
    def test_something_that_is_not_a_summary(self, tmp_path, content):
        path = tmp_path / "s.json"
        path.write_text(content, encoding="utf-8")
        with pytest.raises(k6.SummaryError):
            k6.parse_summary(path)

    def test_a_missing_file(self, tmp_path):
        with pytest.raises(k6.SummaryError):
            k6.parse_summary(tmp_path / "absent.json")


def _metrics(fixture: str) -> k6.LoadMetrics:
    return k6.parse_summary(FIXTURES / fixture)


PASS_PROFILE = _profile(
    ("http_req_duration", "p(95)<500"), ("http_req_failed", "rate<0.01"), ("checks", "rate>0.99")
)


class TestJudge:
    def test_clean_run_passes(self):
        out = k6.judge(
            k6.K6Run(ran=True, exit_code=0), _metrics("k6-summary-pass.json"), PASS_PROFILE
        )
        assert out.status == "pass"
        assert out.metrics["requests"] > 0

    def test_exit_99_fails_and_names_the_breach(self):
        out = k6.judge(
            k6.K6Run(ran=True, exit_code=99),
            _metrics("k6-summary-breach.json"),
            _profile(("http_req_duration", "p(95)<0.001")),
        )
        assert out.status == "fail"
        assert "http_req_duration: p(95)<0.001" in out.reason

    def test_exit_99_without_a_summary_is_still_a_fail(self):
        out = k6.judge(k6.K6Run(ran=True, exit_code=99), None, PASS_PROFILE)
        assert out.status == "fail"
        assert "unreadable" in out.reason

    def test_a_missing_tool_is_unknown(self):
        out = k6.judge(k6.K6Run(ran=False, reason="k6 is not installed"), None, PASS_PROFILE)
        assert out.status == "unknown"

    def test_a_cut_short_run_is_unknown(self):
        out = k6.judge(k6.K6Run(ran=True, timed_out=True, reason="t"), None, PASS_PROFILE)
        assert out.status == "unknown"

    @pytest.mark.parametrize("code", [1, 104, 107, 255])
    def test_other_exit_codes_are_not_verdicts(self, code):
        out = k6.judge(k6.K6Run(ran=True, exit_code=code), None, PASS_PROFILE)
        assert out.status == "unknown"
        assert str(code) in out.reason

    def test_exit_0_with_no_summary_is_unknown(self):
        out = k6.judge(k6.K6Run(ran=True, exit_code=0), None, PASS_PROFILE)
        assert out.status == "unknown"

    def test_zero_requests_is_unknown(self):
        metrics = _metrics("k6-summary-pass.json")
        metrics.requests = 0
        out = k6.judge(k6.K6Run(ran=True, exit_code=0), metrics, PASS_PROFILE)
        assert out.status == "unknown"

    def test_a_threshold_the_summary_does_not_report_is_unknown(self):
        out = k6.judge(
            k6.K6Run(ran=True, exit_code=0),
            _metrics("k6-summary-pass.json"),
            _profile(("http_req_waiting", "p(99)<10")),
        )
        assert out.status == "unknown"
        assert "not evaluated" in out.reason

    def test_a_breach_in_the_summary_fails_even_on_exit_0(self):
        out = k6.judge(
            k6.K6Run(ran=True, exit_code=0),
            _metrics("k6-summary-breach.json"),
            _profile(("http_req_duration", "p(95)<0.001")),
        )
        assert out.status == "fail"


class TestRunner:
    def test_no_routes_is_unknown_and_nothing_runs(self, tmp_path, monkeypatch):
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("k6 started"))
        out = run_load(["/relative", ""], PASS_PROFILE, tmp_path)
        assert out.status == "unknown"
        assert out.findings[0]["rejected_routes"] == ["'/relative'", "''"]

    def test_missing_k6_is_unknown_but_the_script_is_written(self, tmp_path, monkeypatch):
        monkeypatch.delenv("K6_BIN", raising=False)
        monkeypatch.setattr(shutil, "which", lambda name: None)
        out = K6Runner().run(["https://svc.example/a"], PASS_PROFILE, tmp_path)
        assert out.status == "unknown"
        assert "not installed" in out.reason
        assert (tmp_path / "load_test.js").is_file()

    def test_a_measured_run_records_one_row(self, tmp_path):
        sink = MemorySink()
        out = k6.judge(
            k6.K6Run(ran=True, exit_code=0), _metrics("k6-summary-pass.json"), PASS_PROFILE
        )
        row = record(out, ["https://svc.example/a"], PASS_PROFILE, sink)
        assert sink.rows["metric_load_run"] == [row]
        assert json.loads(row["breached_json"]) == []
        assert row["p95_ms"] == out.metrics["p95_ms"]

    def test_an_unmeasured_run_records_nothing(self):
        sink = MemorySink()
        out = k6.judge(k6.K6Run(ran=False, reason="absent"), None, PASS_PROFILE)
        assert record(out, ["https://svc.example/a"], PASS_PROFILE, sink) is None
        assert sink.rows == {}

    def test_cli_without_k6_exits_2(self, tmp_path, monkeypatch, capsys):
        from checks.load.__main__ import main

        monkeypatch.delenv("K6_BIN", raising=False)
        monkeypatch.setattr(shutil, "which", lambda name: None)
        code = main(["--target", "https://svc.example/a", "--out", str(tmp_path), "--no-record"])
        assert code == 2
        assert json.loads(capsys.readouterr().out)["status"] == "unknown"

    def test_cli_with_a_threshold_free_profile_exits_2(self, tmp_path, capsys):
        from checks.load.__main__ import main

        profile = tmp_path / "p.json"
        profile.write_text(json.dumps({"stages": [["1s", 1]], "thresholds": []}))
        code = main(["--target", "https://svc.example/a", "--profile", str(profile)])
        assert code == 2
        assert "nothing to judge" in capsys.readouterr().out
