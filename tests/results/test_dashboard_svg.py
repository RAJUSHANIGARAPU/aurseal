"""SVG helpers produce deterministic output (golden files)."""

from __future__ import annotations

from pathlib import Path

from results.dashboard.svg import line_chart, timeline

GOLDEN = Path(__file__).parent / "golden"


def _chart() -> str:
    return line_chart(
        [("r1", 50.0), ("r2", None), ("r3", 100.0), ("r4", 75.0)],
        y_label="pass rate",
        denominators=["1/2 executed", "0 executed", "2/2 executed", "3/4 executed"],
    )


def _timeline() -> str:
    return timeline([("r1", "passed"), ("r2", "failed"), ("r3", "skipped")], {"r3"})


def test_line_chart_golden():
    assert _chart() == (GOLDEN / "line_chart.svg").read_text()


def test_timeline_golden():
    assert _timeline() == (GOLDEN / "timeline.svg").read_text()


def test_gaps_split_the_line_and_mark_unmeasured():
    svg = _chart()
    assert svg.count("<polyline") == 1  # r1 alone, then r3-r4
    assert "r2: unmeasured (0 executed)" in svg
    assert line_chart([], y_label="x", denominators=[]).endswith("</svg>")
