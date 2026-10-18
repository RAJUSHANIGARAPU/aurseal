"""The recorder under xdist: one row per selected test, written by the controller only."""

from __future__ import annotations

import os

from tests.results._helpers import rows
from tests.results.conftest import PLUGIN

SUITE = """
import pytest

pytest_plugins = ["pytester"]

@pytest.mark.parametrize("n", range(36))
def test_many(n):
    assert n >= 0

def test_fails():
    assert 1 == 2

@pytest.mark.skip(reason="not here")
def test_skipped():
    pass

@pytest.mark.xfail(reason="known")
def test_xfail():
    assert False

def test_ok():
    pass
"""


def test_forty_tests_under_three_workers(pytester, monkeypatch, db_path, tmp_path, subprocess_env):
    open_log = tmp_path / "opens.log"
    monkeypatch.setenv("RESULTS_OPEN_LOG", str(open_log))
    pytester.makepyfile(test_suite=SUITE)
    result = pytester.runpytest_subprocess(*PLUGIN, "-n", "3")
    result.assert_outcomes(passed=37, failed=1, skipped=1, xfailed=1)
    assert "database is locked" not in result.stdout.str() + result.stderr.str()
    result.stdout.fnmatch_lines(["*results-store: 40 result row(s) recorded*"])

    stored = rows(db_path, "SELECT * FROM test_results")
    assert len(stored) == 40
    assert all(r["worker"] and r["worker"].startswith("gw") for r in stored)
    outcomes = {r["nodeid"].split("::")[-1]: r["outcome"] for r in stored}
    assert outcomes["test_fails"] == "failed"
    assert outcomes["test_skipped"] == "skipped"
    assert outcomes["test_xfail"] == "xfailed"
    failed = next(r for r in stored if r["outcome"] == "failed")
    assert failed["message"] and "assert 1 == 2" in failed["message"]
    assert failed["failure_fingerprint"] and failed["longrepr"]
    assert next(r for r in stored if r["outcome"] == "skipped")["skip_reason"] == "not here"
    param = next(r for r in stored if r["nodeid"].endswith("[7]"))
    assert param["param_id"] == "7" and param["duration_ms"] is not None

    # Only the controller ever opened the database: one pid in the open log.
    pids = set(open_log.read_text().split())
    assert len(pids) == 1 and str(os.getpid()) not in pids

    run = rows(db_path, "SELECT * FROM runs")[0]
    assert run["status"] == "finished" and run["source"] == "pytest"
    inv = rows(db_path, "SELECT * FROM invocations")[0]
    assert inv["exit_status"] == 1 and inv["worker_count"] == 3


def test_the_recorder_is_off_when_asked(pytester, monkeypatch, db_path):
    monkeypatch.setenv("RESULTS_STORE", "off")
    pytester.makepyfile("def test_a():\n    pass\n")
    result = pytester.runpytest(*PLUGIN)
    result.assert_outcomes(passed=1)
    assert not db_path.exists()


def test_collect_only_records_nothing(pytester, db_path):
    pytester.makepyfile("def test_a():\n    pass\n")
    pytester.runpytest(*PLUGIN, "--collect-only")
    assert not db_path.exists()
