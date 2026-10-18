"""A closed id in the tracker snapshot turns the known_defect marker off, like the env var."""

from __future__ import annotations

import json

pytest_plugins = ["pytester"]

TEST = """
import pytest

@pytest.mark.known_defect("BUG-1", raises=AssertionError, match="totals differ by a cent")
def test_totals():
    pass  # the fix landed: the oracle passes
"""
ARGS = (
    "-p",
    "verification.pytest_plugin",
    "-p",
    "no:cacheprovider",
    "-W",
    "ignore::pytest.PytestAssertRewriteWarning",
)


def _snapshot(path, state):
    path.write_text(json.dumps({"defects": {"BUG-1": {"id": "BUG-1", "state": state}}}))


def test_open_in_the_snapshot_still_expects_red(pytester, subprocess_env, monkeypatch, tmp_path):
    pytester.makepyfile(test_kd=TEST)
    _snapshot(tmp_path / "s.json", "open")
    monkeypatch.setenv("DEFECT_STATUS_FILE", str(tmp_path / "s.json"))
    result = pytester.runpytest_subprocess(*ARGS)
    result.assert_outcomes(failed=1)  # unexpected pass on an open defect: cleared, loud
    result.stdout.fnmatch_lines(["*BUG-1 looks fixed*"])


def test_closed_in_the_snapshot_makes_it_an_ordinary_test(
    pytester, subprocess_env, monkeypatch, tmp_path
):
    pytester.makepyfile(test_kd=TEST)
    _snapshot(tmp_path / "s.json", "closed")
    monkeypatch.setenv("DEFECT_STATUS_FILE", str(tmp_path / "s.json"))
    pytester.runpytest_subprocess(*ARGS).assert_outcomes(passed=1)


def test_same_as_the_environment_variable(pytester, subprocess_env, monkeypatch):
    pytester.makepyfile(test_kd=TEST)
    monkeypatch.setenv("KNOWN_DEFECTS_CLOSED", "BUG-1")
    pytester.runpytest_subprocess(*ARGS).assert_outcomes(passed=1)
