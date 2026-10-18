"""Per-case trend: pass rate with its denominator, flips, streaks, failing-since."""

from __future__ import annotations

from results.trends import case_history, case_trend, failing_since, fold_outcomes, rate_text
from tests.results._history import add_run


def test_pass_rate_excludes_skip_and_not_run_but_reports_them(store):
    for day, out in enumerate(["passed", "skipped", "failed", "not_run", "passed"], 1):
        add_run(store, f"r{day}", day, {"t": out})
    trend = case_trend(store, "c-t")
    assert (trend.executed, trend.passed, trend.failed) == (3, 2, 1)
    assert (trend.skipped, trend.not_run) == (1, 1)
    assert round(trend.pass_rate, 3) == round(2 / 3, 3)
    assert trend.rate_text == "67% (2/3 executed, 1 skipped, 1 not run)"


def test_a_case_that_only_skips_is_unmeasured_not_green(store):
    for day in range(1, 4):
        add_run(store, f"r{day}", day, {"t": "skipped"})
    trend = case_trend(store, "c-t")
    assert trend.pass_rate is None and trend.unmeasured
    assert trend.rate_text.startswith("unmeasured")
    assert rate_text(0, 0) == "unmeasured (0 executed)"


def test_flips_streak_and_failing_since(store):
    seq = ["passed", "failed", "passed", "failed", "failed", "skipped", "failed"]
    for day, out in enumerate(seq, 1):
        add_run(store, f"r{day}", day, {"t": out})
    trend = case_trend(store, "c-t")
    assert trend.flips == 3
    assert (trend.streak_outcome, trend.streak_length) == ("failed", 3)
    assert trend.failing_since_run_id == "r4"
    assert failing_since(store, "c-t").run_id == "r4"


def test_a_passing_case_is_not_failing_since_anything(store):
    add_run(store, "r1", 1, {"t": "failed"})
    add_run(store, "r2", 2, {"t": "passed"})
    assert failing_since(store, "c-t") is None


def test_window_limits_the_runs_considered(store):
    for day in range(1, 11):
        add_run(store, f"r{day}", day, {"t": "failed" if day <= 5 else "passed"})
    assert case_trend(store, "c-t", window=5).pass_rate == 1.0
    assert case_trend(store, "c-t", window=10).pass_rate == 0.5


def test_retry_masked_passes_and_revision_boundaries_are_visible(store):
    add_run(store, "r1", 1, {"t": "passed"}, revisions={"t": "rev-a"})
    add_run(store, "r2", 2, {"t": ["failed", "passed"]}, revisions={"t": "rev-a"})
    add_run(store, "r3", 3, {"t": "passed"}, revisions={"t": "rev-b"})
    trend = case_trend(store, "c-t")
    assert trend.passed_after_retry == 1 and trend.pass_rate == 1.0
    assert trend.revision_boundaries == ("r3",)
    history = case_history(store, "c-t")
    assert [p.run_id for p in history] == ["r1", "r2", "r3"]
    assert history[1].passed_after_retry


def test_parameter_rows_fold_into_one_case_outcome():
    assert fold_outcomes(["passed", "failed", "skipped"]) == "failed"
    assert fold_outcomes(["passed", "skipped"]) == "passed"
    assert fold_outcomes(["skipped", "xfailed"]) == "xfailed"
    assert fold_outcomes([]) == "not_run"
    assert fold_outcomes(["error", "passed"]) == "error"
