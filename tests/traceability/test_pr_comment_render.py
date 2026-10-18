"""The summary: integrity FAIL leads, missing artefacts say so, truncation keeps the head."""

from __future__ import annotations

import json

from tests.traceability._runs import RunSpec, T, red, write_artifacts
from traceability.pr_comment import (
    MARKER,
    LaneSummary,
    SummaryInputs,
    collect_inputs,
    pr_number,
    render_pr_summary,
)

M = "tests/test_m.py::"


def test_an_integrity_failure_is_the_first_line(tmp_path):
    write_artifacts(
        tmp_path,
        [
            RunSpec("r1", [T(M + "a")], integrity="FAIL", lane="browser"),
            RunSpec("r1", [T(M + "b")], lane="offline"),
        ],
    )
    text = render_pr_summary(collect_inputs([tmp_path], None))
    lines = text.splitlines()
    assert lines[0] == MARKER
    assert lines[1] == "## RUN TRUST FAILED (browser): THIS RUN IS NOT EVIDENCE"


def test_missing_artifacts_are_not_reported_never_zero(tmp_path):
    text = render_pr_summary(collect_inputs([tmp_path / "nothing"], None))
    assert "no run-trust marker was reported" in text
    for what in (
        "Run-trust markers",
        "Verdict file",
        "Flakiness report",
        "Traceability matrix",
        "Gap report",
    ):
        assert f"{what}: _not reported_" in text
    assert "| 0 |" not in text


def test_counts_detectors_matrix_gaps_and_drafts(tmp_path):
    run = RunSpec(
        "r1",
        [T(M + "a"), T(M + "b", "failed"), T(M + "d", draft=True)],
        verdicts=[red("r1", M + "b", "BUG-2", "signature_mismatch"), red("r1", M + "a", "BUG-5")],
    )
    write_artifacts(tmp_path / "now", [run])
    trace = tmp_path / "now" / "traceability"
    trace.mkdir()
    (trace / "matrix.json").write_text(
        json.dumps(
            {
                "headline": "NOTHING IS PROVED",
                "rows": [{"requirement_id": "REQ-1", "status": "FAILING"}],
            }
        )
    )
    (trace / "gaps.json").write_text(json.dumps({"new_gaps": ["openapi:GET /api/x"]}))
    base = tmp_path / "base"
    write_artifacts(base, [RunSpec("r0", [T(M + "a")])])
    (base / "matrix.json").write_text(
        json.dumps({"rows": [{"requirement_id": "REQ-1", "status": "PASSING"}]})
    )
    text = render_pr_summary(collect_inputs([tmp_path / "now"], base))
    assert "| default | PASS | 2 | 1 | 0 | 0 | 0 |" in text
    assert "**signature_mismatch** BUG-2" in text and "newly reproducing: BUG-5" in text
    assert "REQ-1: PASSING → FAILING" in text
    assert "new gap `openapi:GET /api/x`" in text
    assert f"`{M}d`: passed" in text


def test_truncation_keeps_the_headline_and_links_the_reports(monkeypatch):
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.example.test")
    lanes = [LaneSummary(f"lane{i}", "r", "FAIL", "", {"passed": i}) for i in range(500)]
    inputs = SummaryInputs(lanes=lanes, run_url="https://github.example.test/o/r/actions/runs/1")
    text = render_pr_summary(inputs, max_chars=2000)
    assert len(text) <= 2000
    assert text.startswith(MARKER + "\n## RUN TRUST FAILED")
    assert text.rstrip().endswith("actions/runs/1._")


def test_pr_number_and_fork_detection(tmp_path):
    event = tmp_path / "e.json"
    event.write_text(
        json.dumps(
            {
                "pull_request": {
                    "number": 12,
                    "head": {"repo": {"full_name": "someone/fork"}},
                    "base": {"repo": {"full_name": "owner/repo"}},
                }
            }
        )
    )
    assert pr_number(str(event)) == (12, True)
    event.write_text(
        json.dumps(
            {
                "pull_request": {
                    "number": 13,
                    "head": {"repo": {"full_name": "owner/repo"}},
                    "base": {"repo": {"full_name": "owner/repo"}},
                }
            }
        )
    )
    assert pr_number(str(event)) == (13, False)
    assert pr_number(None) == (None, False)
