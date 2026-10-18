"""Only newly flaky tests are listed; without a main baseline the comment says so."""

from __future__ import annotations

import json

from traceability.pr_comment import collect_inputs, render_pr_summary


def _report(directory, *tests):
    d = directory / "flakiness"
    d.mkdir(parents=True)
    (d / "report-20260101T000000Z.json").write_text(
        json.dumps({"flaky_tests": [{"test_id": t} for t in tests]})
    )


def test_only_new_flakes_are_listed(tmp_path):
    _report(tmp_path / "now", "t::old", "t::new")
    _report(tmp_path / "base", "t::old")
    text = render_pr_summary(collect_inputs([tmp_path / "now"], tmp_path / "base"))
    section = text.split("### Newly flaky")[1].split("###")[0]
    assert "`t::new`" in section and "t::old" not in section


def test_no_baseline_is_said(tmp_path):
    _report(tmp_path / "now", "t::a")
    text = render_pr_summary(collect_inputs([tmp_path / "now"], None))
    assert "No baseline from main; 1 flaky test(s) in total." in text
