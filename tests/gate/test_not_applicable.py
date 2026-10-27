"""Out of scope here is a skip, never "could not run": not_applicable, runs_only_on, only_env."""

from __future__ import annotations

import json

import pytest

from gate.integrity import Outcome, RunTrustGate, TestResult, Thresholds, build_tally
from gate.requirement import not_applicable, runs_only_on
from gate.verdicts import NOT_APPLICABLE_PREFIX, VerdictRow
from verification.verdicts import read_verdicts

pytest_plugins = ["pytester"]

PLUGIN = (
    "-p",
    "gate.pytest_plugin",
    "-W",
    "ignore::pytest.PytestAssertRewriteWarning",
    "-p",
    "no:cacheprovider",
)


@pytest.fixture
def verdicts_file(tmp_path, monkeypatch):
    path = tmp_path / "verdicts.jsonl"
    monkeypatch.setenv("VERDICTS_FILE", str(path))
    monkeypatch.setenv("TEST_RUN_ID", "run-na")
    return path


def test_in_scope_returns(monkeypatch, verdicts_file):
    monkeypatch.setenv("TEST_ENV", "staging")
    runs_only_on({"qa", "staging"}, "the export check")
    assert not verdicts_file.exists()


def test_explicit_env_wins_over_the_variable(monkeypatch, verdicts_file):
    monkeypatch.setenv("TEST_ENV", "staging")
    runs_only_on({"qa"}, "the export check", "qa")


def test_out_of_scope_skips_with_the_reason_and_records_a_verdict(monkeypatch, verdicts_file):
    monkeypatch.setenv("TEST_ENV", "local")
    with pytest.raises(pytest.skip.Exception) as skipped:
        runs_only_on({"staging", "qa"}, "the export check")
    message = str(skipped.value)
    assert message.startswith(NOT_APPLICABLE_PREFIX)
    assert "the export check only runs on ['qa', 'staging'], not on 'local'" in message
    rows = read_verdicts(verdicts_file)
    assert [(r["kind"], r["verdict"]) for r in rows] == [("coverage", "not_applicable")]
    assert "covered by the same check on qa, staging" in rows[0]["detail"]


@pytest.mark.parametrize("switch", ["VERDICT_REQUIRED", "SUITE_REQUIRED"])
def test_required_lanes_still_skip(monkeypatch, verdicts_file, switch):
    """The point of the category: a lane that requires verdicts must not fail on it."""
    monkeypatch.setenv(switch, "true")
    monkeypatch.setenv("TEST_ENV", "qa")
    with pytest.raises(pytest.skip.Exception):
        runs_only_on({"staging"}, "the export check")


def test_not_applicable_names_where_it_is_covered(verdicts_file):
    with pytest.raises(pytest.skip.Exception, match=r"covered by the nightly staging lane"):
        not_applicable("no mail relay here", covered_by="the nightly staging lane")
    with pytest.raises(ValueError, match="covered_by"):
        not_applicable("no mail relay here", covered_by=" ")


def test_runs_only_on_needs_an_environment():
    with pytest.raises(ValueError):
        runs_only_on(set(), "x")


def _result(nodeid: str, outcome: Outcome, reason: str = "") -> TestResult:
    return TestResult(nodeid, outcome, (), reason)


def test_tally_lists_not_applicable_apart_from_skips():
    tests = [
        _result("t::a", Outcome.PASSED),
        _result("t::b", Outcome.SKIPPED, NOT_APPLICABLE_PREFIX + "only on qa"),
        _result("t::c", Outcome.SKIPPED, "renderer unreachable"),
        # The verdict row alone is enough (a reason rewritten by another plugin).
        _result("t::d", Outcome.SKIPPED, "something else"),
    ]
    rows = [VerdictRow("r", "t::d", "coverage", None, "not_applicable")]
    tally = build_tally(tests, rows)
    assert tally.skipped == 1 and [s.test for s in tally.silences] == ["t::c"]
    assert [s.test for s in tally.out_of_scope] == ["t::b", "t::d"]
    assert tally.in_scope == 2 and tally.skip_ratio == 0.5


def test_an_all_out_of_scope_selection_is_not_a_skip_ratio_breach():
    tests = [_result(f"t::{n}", Outcome.SKIPPED, NOT_APPLICABLE_PREFIX + "x") for n in range(3)]
    gate = RunTrustGate(Thresholds(min_executed=0, max_skip_ratio=0.1))
    result = gate.evaluate(build_tally(tests))
    assert not result.breaches
    assert any("out of scope" in note for note in result.not_applicable)


ONLY_ENV = """
import pytest

@pytest.mark.only_env("staging", covered_by="the staging nightly")
def test_staging_only():
    pass

def test_everywhere():
    pass
"""


def test_marker_skips_out_of_scope_in_a_required_lane(pytester, monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_ENV", "qa")
    monkeypatch.setenv("VERDICT_REQUIRED", "true")
    monkeypatch.setenv("RUN_TRUST", "fail")
    monkeypatch.setenv("RUN_TRUST_MAX_SKIP_RATIO", "0.1")
    monkeypatch.setenv("RUN_TRUST_MIN_EXECUTED", "1")
    monkeypatch.setenv("VERDICTS_FILE", str(tmp_path / "v.jsonl"))
    pytester.makepyfile(ONLY_ENV)
    result = pytester.runpytest(*PLUGIN, "-rs")
    assert result.ret == 0, result.stdout.str()
    result.assert_outcomes(passed=1, skipped=1)
    result.stdout.fnmatch_lines(["*not applicable here: test_staging_only only runs on*"])
    marker = json.loads((pytester.path / "reports" / "run-trust.json").read_text())
    assert marker["skipped"] == 0 and len(marker["out_of_scope"]) == 1


def test_marker_runs_in_scope(pytester, monkeypatch):
    monkeypatch.setenv("TEST_ENV", "staging")
    pytester.makepyfile(ONLY_ENV)
    pytester.runpytest(*PLUGIN).assert_outcomes(passed=2)
