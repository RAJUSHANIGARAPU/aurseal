"""An all-skip run renders "unmeasured", never a percentage."""

from __future__ import annotations

import re

from results.dashboard import render
from tests.results._history import add_run


def test_all_skip_run_is_unmeasured(store, tmp_path):
    add_run(store, "skips", 1, {"a": "skipped", "b": "skipped"}, verdict="FAIL")
    out = tmp_path / "dash"
    render(store, out)
    index = (out / "index.html").read_text()
    run = (out / "runs" / "skips.html").read_text()
    for html in (index, run):
        assert "unmeasured (0 executed, 2 skipped)" in html
        # Axis labels and CSS widths are not rates.
        text = re.sub(r"<(svg|style)\b.*?</\1>", "", html, flags=re.S)
        assert "100%" not in text
        assert not re.search(r"\d+%(?! \()", text)  # every percentage shows its denominator
    assert "skips: unmeasured" in index  # the chart point says so too


def test_rates_always_carry_their_denominator(store, tmp_path):
    add_run(store, "r1", 1, {"a": "passed", "b": "failed", "c": "skipped"})
    out = tmp_path / "dash"
    render(store, out)
    assert "50% (1/2 executed, 1 skipped)" in (out / "index.html").read_text()
