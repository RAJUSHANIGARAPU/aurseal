"""A load test against a placeholder is not evidence, and a missing tool is not a skip.

``load-performance`` reported ``pass`` in two situations where it had measured
nothing about the product. Given no routes it substituted ``["http://localhost"]``
and load tested that; given no k6 on PATH it returned ``skip``, which the old
scoring counted toward health.

The controls matter as much as the failures here. Every assertion below is
satisfied by a plugin that has given up and answers ``unknown`` to everything,
so ``TestItStillMeasures`` pins the two outcomes that require it to have
actually run k6 — including the ``fail`` that makes this the one tier-3 plugin
able to report a problem with the product.

k6 is never executed. ``shutil.which`` and ``subprocess.run`` are both replaced,
so the test is identical on a machine that has k6 and one that does not.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tests.plugins._tier3_support import StubGovernor, load_plugin

_SCRIPT = Path("ai_generated_tests/load/load_test.js")


@pytest.fixture
def plugin():
    return load_plugin("load_performance.plugin.py", "LoadPerformancePlugin")


@pytest.fixture(autouse=True)
def _sandbox(tmp_path, monkeypatch):
    """Keep generated scripts out of the repo, and k6 out of the run."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: pytest.fail("k6 must not be executed by a test")
    )


def _k6(monkeypatch, returncode: int = 0, raises: Exception | None = None) -> None:
    """Pretend k6 is installed and make `k6 run` return `returncode`."""
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/local/bin/k6" if name == "k6" else None)

    class _Completed:
        def __init__(self) -> None:
            self.returncode = returncode
            self.stdout = "checks.........: 90.00%\n"
            self.stderr = ""

    def _run(*args, **kwargs):  # noqa: ANN002, ANN003, ARG001
        if raises is not None:
            raise raises
        return _Completed()

    monkeypatch.setattr(subprocess, "run", _run)


def _ctx(**overrides) -> dict:
    # A governor is supplied even though the current plugin has no model call:
    # it keeps this suite offline against any version of the plugin, including
    # the one these tests were first run against.
    return {"cost_governor": StubGovernor(""), **overrides}


class TestNoRoutesIsNotAVerdict:
    @pytest.mark.parametrize("routes", [[], None, (), "http://x"])
    def test_an_absent_route_list_is_unknown(self, plugin, routes):
        result = plugin.run(_ctx(routes=routes))
        assert result.status == "unknown"

    def test_it_does_not_invent_a_target(self, plugin):
        """The placeholder is the whole defect — a run that measured localhost
        must not be able to look like a run that measured the product."""
        result = plugin.run(_ctx(routes=[]))
        assert "localhost" not in str(result.findings)
        assert not _SCRIPT.exists(), "a script was written for routes nobody supplied"

    def test_routes_that_k6_cannot_fetch_are_reported_not_used(self, plugin):
        result = plugin.run(_ctx(routes=["/health", "", "example.com"]))
        assert result.status == "unknown"
        assert result.findings[0]["rejected_routes"] == ["'/health'", "''", "'example.com'"]


class TestAMissingToolIsUnknownNotSkip:
    def test_absent_k6_is_unknown(self, plugin):
        result = plugin.run(_ctx(routes=["https://svc.example/health"]))
        assert result.status == "unknown"
        assert "k6" in result.findings[0]["reason"]

    def test_the_script_is_still_written_for_a_human_to_run(self, plugin):
        """Unknown is a verdict about the measurement, not a reason to bin the
        artefact — the script is the one useful thing the run produced."""
        plugin.run(_ctx(routes=["https://svc.example/health"]))
        assert _SCRIPT.exists()

    def test_a_cut_short_run_is_unknown(self, plugin, monkeypatch):
        _k6(monkeypatch, raises=subprocess.TimeoutExpired(cmd="k6", timeout=120))
        result = plugin.run(_ctx(routes=["https://svc.example/health"]))
        assert result.status == "unknown"


class TestItStillMeasures:
    """Positive controls. A plugin hardwired to ``unknown`` passes everything
    above and measures nothing at all."""

    def test_a_clean_k6_run_passes(self, plugin, monkeypatch):
        _k6(monkeypatch, returncode=0)
        result = plugin.run(_ctx(routes=["https://svc.example/health"]))
        assert result.status == "pass"
        assert result.findings[0]["exit_code"] == 0

    def test_a_breached_threshold_is_a_fail(self, plugin, monkeypatch):
        """k6 exits non-zero on a failed check or threshold. That is a finding
        about the product, and it is the only ``fail`` tier 3 can produce."""
        _k6(monkeypatch, returncode=99)
        result = plugin.run(_ctx(routes=["https://svc.example/health"]))
        assert result.status == "fail"
        assert result.findings[0]["exit_code"] == 99

    def test_the_executed_script_targets_the_supplied_routes(self, plugin, monkeypatch):
        _k6(monkeypatch, returncode=0)
        plugin.run(_ctx(routes=["https://svc.example/health", "https://svc.example/api"]))
        script = _SCRIPT.read_text(encoding="utf-8")
        assert "https://svc.example/health" in script
        assert "https://svc.example/api" in script
        assert "localhost" not in script

    def test_a_dry_run_reports_the_script_it_would_use(self, plugin):
        result = plugin.run(_ctx(routes=["https://svc.example/health"], dry_run=True))
        assert result.status == "pass"
        assert result.dry_run is True


class TestTheScriptIsNotModelOutput:
    def test_the_model_is_never_asked(self, plugin, monkeypatch):
        """There is no JavaScript parser here, so a model reply could never be
        checked before `k6 run` was handed it. The call is gone; if it comes
        back, so does the arbitrary-code path."""
        _k6(monkeypatch, returncode=0)
        governor = StubGovernor("export default function () {}")
        plugin.run({"routes": ["https://svc.example/health"], "cost_governor": governor})
        assert governor.calls == 0

    def test_the_script_is_valid_k6_shape(self, plugin, monkeypatch):
        _k6(monkeypatch, returncode=0)
        plugin.run(_ctx(routes=["https://svc.example/health"]))
        script = _SCRIPT.read_text(encoding="utf-8")
        assert "import http from 'k6/http';" in script
        assert "export default function ()" in script
