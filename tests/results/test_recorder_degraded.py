"""A store failure never changes a test outcome, and is never silent."""

from __future__ import annotations

import json

from tests.results.conftest import PLUGIN

pytest_plugins = ["pytester"]


def _unwritable(tmp_path, monkeypatch):
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where a directory should be")
    monkeypatch.setenv("RESULTS_DB", str(blocker / "sub" / "results.db"))


def test_degraded_store_keeps_the_exit_status_and_says_so(pytester, tmp_path, monkeypatch):
    _unwritable(tmp_path, monkeypatch)
    pytester.makepyfile("def test_a():\n    pass\n")
    result = pytester.runpytest(*PLUGIN)
    assert result.ret == 0
    result.stdout.fnmatch_lines(["*results-store: DEGRADED (cannot open the store*"])
    status = json.loads((tmp_path / "results-dir" / "store-status.json").read_text())
    assert status["status"] == "degraded" and status["results_written"] == 0


def test_required_store_turns_degraded_into_a_non_zero_exit(pytester, tmp_path, monkeypatch):
    _unwritable(tmp_path, monkeypatch)
    monkeypatch.setenv("RESULTS_STORE_REQUIRED", "true")
    pytester.makepyfile("def test_a():\n    pass\n")
    result = pytester.runpytest(*PLUGIN)
    assert result.ret != 0
    result.stdout.fnmatch_lines(["*DEGRADED*"])


def test_failing_tests_keep_their_own_exit_code_when_degraded(pytester, tmp_path, monkeypatch):
    _unwritable(tmp_path, monkeypatch)
    pytester.makepyfile("def test_a():\n    assert False\n")
    result = pytester.runpytest(*PLUGIN)
    assert result.ret == 1


def test_healthy_store_writes_an_ok_status(pytester, tmp_path, db_path):
    pytester.makepyfile("def test_a():\n    pass\n")
    pytester.runpytest(*PLUGIN)
    status = json.loads((tmp_path / "results-dir" / "store-status.json").read_text())
    assert status["status"] == "ok" and status["results_written"] == 1
