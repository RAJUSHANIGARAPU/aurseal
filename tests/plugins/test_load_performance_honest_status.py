"""A load test against a placeholder is not evidence, and a missing tool is not a skip.

``load-performance`` is now a thin adapter over ``checks.load``. The rules it keeps:
no routes or no thresholds ⇒ ``unknown``; k6 absent or cut short ⇒ ``unknown``; a
crossed threshold ⇒ ``fail`` naming it; a dry run ⇒ ``skip``, never ``pass``.

The controls matter as much as the failures: ``TestItStillMeasures`` pins the outcomes
that need k6 to have run. k6 is never executed here: ``shutil.which`` and
``subprocess.run`` are replaced, and the "summary" k6 would write is a recorded one.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tests.plugins._tier3_support import StubGovernor, load_plugin

FIXTURES = Path(__file__).resolve().parents[1] / "checks" / "fixtures"
PROFILE = {
    "stages": [["1s", 2]],
    "thresholds": [
        {"metric": "http_req_duration", "expr": "p(95)<500"},
        {"metric": "http_req_failed", "expr": "rate<0.01"},
        {"metric": "checks", "expr": "rate>0.99"},
    ],
}
BREACH_PROFILE = {
    "stages": [["1s", 2]],
    "thresholds": [{"metric": "http_req_duration", "expr": "p(95)<0.001"}],
}
ROUTE = "https://svc.example/health"


@pytest.fixture
def plugin():
    return load_plugin("load_performance.plugin.py", "LoadPerformancePlugin")


@pytest.fixture(autouse=True)
def _sandbox(monkeypatch):
    """No k6 on PATH and no subprocess, unless a test installs a fake."""
    monkeypatch.delenv("K6_BIN", raising=False)
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: pytest.fail("k6 must not be executed by a test")
    )


def _k6(monkeypatch, returncode: int = 0, summary: str | None = None, raises=None) -> list:
    """Pretend k6 is installed; ``k6 run`` copies a recorded summary and exits ``returncode``."""
    monkeypatch.setattr(shutil, "which", lambda name: "/opt/k6/k6" if name == "k6" else None)
    calls: list[list[str]] = []

    class _Done:
        def __init__(self, code: int, out: str = "") -> None:
            self.returncode = code
            self.stdout = out
            self.stderr = ""

    def _run(cmd, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ARG001
        calls.append(cmd)
        if cmd[1] == "version":
            return _Done(0, "k6 v2.3.0 (commit/x, go, darwin/arm64)")
        if raises is not None:
            raise raises
        if summary is not None:
            target = Path(cmd[cmd.index("--summary-export") + 1])
            target.write_text((FIXTURES / summary).read_text(encoding="utf-8"), encoding="utf-8")
        return _Done(returncode)

    monkeypatch.setattr(subprocess, "run", _run)
    return calls


def _ctx(tmp_path, **overrides) -> dict:
    return {
        "cost_governor": StubGovernor(""),
        "profile": PROFILE,
        "out_dir": str(tmp_path),
        **overrides,
    }


class TestNoRoutesIsNotAVerdict:
    @pytest.mark.parametrize("routes", [[], None, (), "http://x"])
    def test_an_absent_route_list_is_unknown(self, plugin, tmp_path, routes):
        assert plugin.run(_ctx(tmp_path, routes=routes)).status == "unknown"

    def test_it_does_not_invent_a_target(self, plugin, tmp_path):
        result = plugin.run(_ctx(tmp_path, routes=[]))
        assert "localhost" not in str(result.findings)
        assert not (tmp_path / "load_test.js").exists()

    def test_routes_that_k6_cannot_fetch_are_reported_not_used(self, plugin, tmp_path):
        result = plugin.run(_ctx(tmp_path, routes=["/health", "", "example.com"]))
        assert result.status == "unknown"
        assert result.findings[0]["findings"][0]["rejected_routes"] == [
            "'/health'",
            "''",
            "'example.com'",
        ]


class TestNoThresholdsIsNotAVerdict:
    def test_no_profile_is_unknown(self, plugin, tmp_path):
        result = plugin.run(_ctx(tmp_path, routes=[ROUTE], profile=None))
        assert result.status == "unknown"
        assert "thresholds" in result.findings[0]["reason"]

    def test_a_profile_without_thresholds_is_unknown(self, plugin, tmp_path):
        result = plugin.run(
            _ctx(tmp_path, routes=[ROUTE], profile={"stages": [["1s", 1]], "thresholds": []})
        )
        assert result.status == "unknown"
        assert "nothing to judge" in result.findings[0]["reason"]


class TestAMissingToolIsUnknownNotSkip:
    def test_absent_k6_is_unknown(self, plugin, tmp_path):
        result = plugin.run(_ctx(tmp_path, routes=[ROUTE]))
        assert result.status == "unknown"
        assert "k6" in result.findings[0]["reason"]

    def test_the_script_is_still_written_for_a_human_to_run(self, plugin, tmp_path):
        plugin.run(_ctx(tmp_path, routes=[ROUTE]))
        assert ROUTE in (tmp_path / "load_test.js").read_text(encoding="utf-8")

    def test_a_cut_short_run_is_unknown(self, plugin, tmp_path, monkeypatch):
        _k6(monkeypatch, raises=subprocess.TimeoutExpired(cmd="k6", timeout=1))
        assert plugin.run(_ctx(tmp_path, routes=[ROUTE])).status == "unknown"

    def test_an_unparseable_summary_is_unknown(self, plugin, tmp_path, monkeypatch):
        _k6(monkeypatch, returncode=0, summary=None)
        result = plugin.run(_ctx(tmp_path, routes=[ROUTE]))
        assert result.status == "unknown"
        assert "summary" in result.findings[0]["reason"]

    def test_a_script_error_exit_is_unknown_not_fail(self, plugin, tmp_path, monkeypatch):
        _k6(monkeypatch, returncode=107)
        assert plugin.run(_ctx(tmp_path, routes=[ROUTE])).status == "unknown"


class TestItStillMeasures:
    """Positive controls: a plugin hardwired to ``unknown`` passes everything above."""

    def test_a_clean_k6_run_passes(self, plugin, tmp_path, monkeypatch):
        _k6(monkeypatch, returncode=0, summary="k6-summary-pass.json")
        result = plugin.run(_ctx(tmp_path, routes=[ROUTE]))
        assert result.status == "pass"
        assert result.findings[0]["metrics"]["requests"] > 0

    def test_a_breached_threshold_is_a_fail_naming_it(self, plugin, tmp_path, monkeypatch):
        _k6(monkeypatch, returncode=99, summary="k6-summary-breach.json")
        result = plugin.run(_ctx(tmp_path, routes=[ROUTE], profile=BREACH_PROFILE))
        assert result.status == "fail"
        assert "http_req_duration: p(95)<0.001" in result.findings[0]["reason"]

    def test_the_executed_script_targets_the_supplied_routes(self, plugin, tmp_path, monkeypatch):
        calls = _k6(monkeypatch, returncode=0, summary="k6-summary-pass.json")
        plugin.run(_ctx(tmp_path, routes=[ROUTE, "https://svc.example/api"]))
        script = (tmp_path / "load_test.js").read_text(encoding="utf-8")
        assert ROUTE in script and "https://svc.example/api" in script
        assert "localhost" not in script
        run_cmd = next(c for c in calls if c[1] == "run")
        assert "--summary-export" in run_cmd

    def test_a_dry_run_is_skip_and_starts_nothing(self, plugin, tmp_path):
        result = plugin.run(_ctx(tmp_path, routes=[ROUTE], dry_run=True))
        assert result.status == "skip"
        assert result.dry_run is True
        assert Path(result.findings[0]["script_path"]).is_file()


class TestTheScriptIsNotModelOutput:
    def test_the_model_is_never_asked(self, plugin, tmp_path, monkeypatch):
        _k6(monkeypatch, returncode=0, summary="k6-summary-pass.json")
        governor = StubGovernor("export default function () {}")
        plugin.run({**_ctx(tmp_path, routes=[ROUTE]), "cost_governor": governor})
        assert governor.calls == 0
