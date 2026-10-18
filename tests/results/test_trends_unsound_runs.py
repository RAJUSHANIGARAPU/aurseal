"""A run the integrity gate judged FAIL is excluded from windows unless asked for."""

from __future__ import annotations

from results.trends import case_trend, suite_trend, unstable
from tests.results._history import add_run


def test_unsound_run_is_excluded_by_default_and_included_on_request(store):
    add_run(store, "r1", 1, {"t": "failed"})
    add_run(store, "r2", 2, {"t": "passed"}, verdict="FAIL")  # executed almost nothing
    add_run(store, "r3", 3, {"t": "failed"})
    default = case_trend(store, "c-t")
    assert default.excluded_unsound == 1 and default.pass_rate == 0.0
    included = case_trend(store, "c-t", include_unsound=True)
    assert included.excluded_unsound == 0 and round(included.pass_rate, 2) == 0.33


def test_suite_trend_flags_unsound_runs_and_carries_denominators(store):
    add_run(store, "r1", 1, {"a": "passed", "b": "skipped"})
    add_run(store, "r2", 2, {"a": "skipped", "b": "skipped"}, verdict="FAIL")
    points = suite_trend(store, lane="offline")
    assert [p.run_id for p in points] == ["r1", "r2"]
    assert points[0].rate_text == "100% (1/1 executed, 1 skipped)"
    assert points[1].unsound and points[1].pass_rate is None
    assert points[1].rate_text.startswith("unmeasured")
    assert points[0].wall_time_s == 300.0 and points[0].executed_ratio == 0.5


def test_unstable_ignores_unsound_runs(store):
    add_run(store, "r1", 1, {"t": "passed"})
    add_run(store, "r2", 2, {"t": "failed"}, verdict="FAIL")
    add_run(store, "r3", 3, {"t": "passed"})
    assert unstable(store, min_flips=1) == []
