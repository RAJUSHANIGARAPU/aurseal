"""Duration percentiles and drift detection on synthetic durations."""

from __future__ import annotations

from results.trends import HistoryPoint, percentile, trend_from_points


def _points(durations):
    return [
        HistoryPoint(f"r{i}", None, "passed", 1, d, None, None, None, "PASS", None)
        for i, d in enumerate(durations)
    ]


def test_percentiles_nearest_rank():
    assert percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 50) == 5
    assert percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 95) == 10
    assert percentile([], 50) is None


def test_drift_is_flagged_above_the_threshold():
    trend = trend_from_points("c", _points([100] * 20 + [200] * 5), drift_threshold=0.5)
    assert trend.duration_drift == 1.0 and trend.drift_flagged


def test_small_drift_is_not_flagged():
    trend = trend_from_points("c", _points([100] * 20 + [120] * 5), drift_threshold=0.5)
    assert round(trend.duration_drift, 2) == 0.2 and not trend.drift_flagged


def test_threshold_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("RESULTS_DURATION_DRIFT", "0.1")
    trend = trend_from_points("c", _points([100] * 20 + [120] * 5))
    assert trend.drift_flagged


def test_too_little_history_has_no_drift():
    trend = trend_from_points("c", _points([100, 300]))
    assert trend.duration_drift is None and not trend.drift_flagged
    assert trend.duration_p50_ms == 100 and trend.duration_p95_ms == 300
