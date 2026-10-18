"""Reruns are attempts; a second invocation of the same run is an invocation, not a run."""

from __future__ import annotations

from tests.results._helpers import rows
from tests.results.conftest import PLUGIN

FLAKY = """
import pathlib

pytest_plugins = ["pytester"]

COUNTER = pathlib.Path(__file__).with_name("count.txt")

def test_flaky():
    n = int(COUNTER.read_text()) if COUNTER.exists() else 0
    COUNTER.write_text(str(n + 1))
    assert n >= 2, f"attempt {n + 1} fails"

def test_stable():
    pass
"""


def test_reruns_store_every_attempt_and_final_results_is_the_last(pytester, db_path):
    pytester.makepyfile(test_flaky=FLAKY)
    result = pytester.runpytest(*PLUGIN, "--reruns", "2")
    result.assert_outcomes(passed=2)
    assert "2 rerun" in result.stdout.str()
    attempts = rows(
        db_path,
        "SELECT attempt, outcome FROM test_results WHERE nodeid LIKE '%test_flaky'"
        " ORDER BY attempt",
    )
    assert attempts == [
        {"attempt": 1, "outcome": "failed"},
        {"attempt": 2, "outcome": "failed"},
        {"attempt": 3, "outcome": "passed"},
    ]
    final = rows(db_path, "SELECT outcome FROM final_results WHERE nodeid LIKE '%test_flaky'")
    assert final == [{"outcome": "passed"}]


def test_a_second_invocation_with_the_same_run_id_adds_an_invocation(pytester, db_path):
    pytester.makepyfile(test_a="def test_a():\n    assert False\n\ndef test_b():\n    pass\n")
    pytester.runpytest(*PLUGIN)
    pytester.makepyfile(test_a="def test_a():\n    pass\n\ndef test_b():\n    pass\n")
    pytester.runpytest(*PLUGIN, "test_a.py::test_a")
    assert len(rows(db_path, "SELECT * FROM runs")) == 1
    assert [r["seq"] for r in rows(db_path, "SELECT seq FROM invocations ORDER BY seq")] == [1, 2]
    a = rows(
        db_path,
        "SELECT tr.attempt, tr.outcome, i.seq FROM test_results tr"
        " JOIN invocations i ON i.id = tr.invocation_id"
        " WHERE tr.nodeid LIKE '%test_a' ORDER BY tr.attempt",
    )
    assert a == [
        {"attempt": 1, "outcome": "failed", "seq": 1},
        {"attempt": 2, "outcome": "passed", "seq": 2},
    ]
    final = {
        r["nodeid"].split("::")[-1]: r["outcome"]
        for r in rows(db_path, "SELECT * FROM final_results")
    }
    assert final == {"test_a": "passed", "test_b": "passed"}


def test_selected_but_never_reported_is_stored_as_not_run(pytester, db_path):
    pytester.makepyfile(
        test_x="def test_1():\n    assert False\n\n"
        "def test_2():\n    pass\n\ndef test_3():\n    pass\n"
    )
    pytester.runpytest(*PLUGIN, "-x")
    stored = rows(db_path, "SELECT * FROM final_results")
    final = {r["nodeid"].split("::")[-1]: r for r in stored}
    assert final["test_1"]["outcome"] == "failed"
    assert final["test_2"]["outcome"] == "not_run" and final["test_3"]["outcome"] == "not_run"
    assert "never reported" in final["test_2"]["message"]


def test_setup_and_teardown_errors_are_error(pytester, db_path):
    pytester.makepyfile(
        """
        import pytest

        @pytest.fixture
        def broken():
            raise RuntimeError("setup broke")

        @pytest.fixture
        def bad_teardown():
            yield
            raise RuntimeError("teardown broke")

        def test_setup(broken):
            pass

        def test_teardown(bad_teardown):
            pass
        """
    )
    pytester.runpytest(*PLUGIN)
    got = {r["nodeid"].split("::")[-1]: r for r in rows(db_path, "SELECT * FROM test_results")}
    assert got["test_setup"]["outcome"] == "error" and "setup broke" in got["test_setup"]["message"]
    assert got["test_teardown"]["outcome"] == "error"
    assert "teardown broke" in got["test_teardown"]["message"]


def test_run_metadata_from_ci_environment(pytester, db_path, monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_SHA", "0123456789abcdef0123456789abcdef01234567")
    monkeypatch.setenv("GITHUB_REF_NAME", "main")
    monkeypatch.setenv("GITHUB_RUN_ID", "77")
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/repo")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("RESULTS_LANE", "offline")
    pytester.makepyfile("def test_a():\n    pass\n")
    pytester.runpytest(*PLUGIN)
    run = rows(db_path, "SELECT * FROM runs")[0]
    assert run["git_branch"] == "main" and run["lane"] == "offline" and run["trigger"] == "push"
    assert run["ci_provider"] == "github-actions" and run["ci_url"].endswith("/actions/runs/77")
    assert run["git_sha"].startswith("0123456")


def test_integrity_verdict_and_side_files_are_ingested(pytester, db_path, monkeypatch):
    monkeypatch.setenv("RUN_TRUST", "report")
    pytester.makepyfile(
        """
        from verification.verdicts import record_verdict

        def test_a():
            record_verdict("coverage", "proved", subject="REQ-12")
        """
    )
    pytester.runpytest(*PLUGIN, "-p", "gate.pytest_plugin")
    run = rows(db_path, "SELECT * FROM runs")[0]
    assert run["integrity_verdict"] in ("PASS", "FAIL")
    verdicts = rows(db_path, "SELECT * FROM verdicts")
    assert [(v["kind"], v["verdict"], v["subject"]) for v in verdicts] == [
        ("coverage", "proved", "REQ-12")
    ]
