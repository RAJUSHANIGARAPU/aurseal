"""Evidence collection, merge and publishing (gate.evidence)."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from gate.evidence import (
    EVIDENCE,
    EVIDENCE_PATHS,
    PREFIX,
    absent,
    expected_from_needs,
    junit_summary,
    junit_totals,
    main,
    merge_results,
    publish,
    stage,
    write_categories_into,
)

ROOT = Path(__file__).resolve().parents[2]

JUNIT = """<?xml version="1.0"?>
<testsuites><testsuite name="s">
  <testcase classname="t" name="ok"/>
  <testcase classname="t" name="bad"><failure message="x"/></testcase>
  <testcase classname="t" name="broken"><error message="y"/></testcase>
  <testcase classname="t" name="later"><skipped/></testcase>
</testsuite></testsuites>
"""


def test_categories_are_written_into_every_directory(tmp_path):
    dirs = [tmp_path / "allure-results", tmp_path / "allure-results-render" / "deep"]
    written = write_categories_into(dirs)
    assert [p.parent for p in written] == dirs
    assert all(json.loads(p.read_text()) for p in written)


def test_merge_keeps_both_same_named_results(tmp_path, capsys):
    for lane, body in (("offline", "{}"), ("browser", '{"x": 1}')):
        results = tmp_path / "in" / f"{PREFIX}{lane}" / "reports" / "allure-results"
        results.mkdir(parents=True)
        (results / "abc-result.json").write_text(body)
    artefacts = {
        "browser": tmp_path / "in" / f"{PREFIX}browser",
        "offline": tmp_path / "in" / f"{PREFIX}offline",
    }
    merge = merge_results(artefacts, tmp_path / "out")
    names = sorted(p.name for p in (tmp_path / "out").iterdir())
    assert names == ["abc-result.json", "evidence-offline--abc-result.json"]
    assert merge.renames == [
        "abc-result.json from evidence-offline renamed to evidence-offline--abc-result.json"
    ]


def test_junit_summary_per_lane_and_capped_failures(tmp_path):
    (tmp_path / "a.xml").write_text(JUNIT)
    browser = junit_totals("browser", [tmp_path / "a.xml"])
    offline = junit_totals("offline", [tmp_path / "a.xml", tmp_path / "a.xml"])
    assert (browser.totals.tests, browser.totals.failures, browser.totals.errors) == (4, 1, 1)
    assert browser.totals.skipped == 1 and offline.totals.tests == 8
    lines = junit_summary([browser, offline], cap=3)
    assert "| browser | 4 | 1 | 1 | 1 |" in lines and "| offline | 8 | 2 | 2 | 2 |" in lines
    assert lines[-1] == "- ... and 3 more"
    assert sum(1 for line in lines if line.startswith("- ") and "::" in line) == 3


def test_missing_artefacts_are_listed():
    lines = absent(["offline", "browser", "render"], ["offline"])
    assert len(lines) == 2 and "evidence-browser" in lines[0] and "evidence-render" in lines[1]
    assert absent(["browser-matrix"], ["browser-matrix--firefox"]) == []
    expected, skipped = expected_from_needs(
        {"offline": {"result": "success"}, "render": {"result": "skipped"}}
    )
    assert expected == ["offline"] and skipped == ["render"]


def test_publishing_with_no_inputs_exits_0_and_says_so(tmp_path):
    summary = tmp_path / "summary.md"
    code = main(
        [
            "publish",
            "--in",
            str(tmp_path / "none"),
            "--out",
            str(tmp_path / "out"),
            "--summary",
            str(summary),
            "--expected",
            "offline",
        ],
        environ={},
    )
    assert code == 0
    text = summary.read_text()
    assert "No evidence was found" in text and "absent: evidence-offline" in text


def test_publish_end_to_end_with_trust_first_and_a_rendered_report(tmp_path):
    reports = tmp_path / "in" / f"{PREFIX}offline" / "reports"
    (reports / "allure-results").mkdir(parents=True)
    (reports / "junit-offline.xml").write_text(JUNIT)
    (reports / "run-trust-offline.txt").write_text(
        "VERDICT=PASS ENFORCEMENT=FAIL RUN=r LANE=offline\nbox\n"
    )
    lines = publish(tmp_path / "in", tmp_path / "out", expected=["offline", "browser"])
    assert lines[2] == "- offline: VERDICT=PASS ENFORCEMENT=FAIL RUN=r LANE=offline"
    assert any(line.startswith("absent: evidence-browser") for line in lines)
    assert (tmp_path / "out" / "report" / "index.html").is_file()


def test_stage_copies_the_listed_evidence(tmp_path):
    reports = tmp_path / "reports"
    (reports / "traces").mkdir(parents=True)
    (reports / "traces" / "t.zip").write_bytes(b"zip")
    (reports / "run-trust-browser.txt").write_text("VERDICT=PASS")
    (reports / "unrelated.db").write_text("x")
    copied = stage("browser", tmp_path, "evidence")
    staged = tmp_path / "evidence"
    assert (staged / "reports" / "traces" / "t.zip").is_file()
    assert (staged / "reports" / "run-trust-browser.txt").is_file()
    assert not (staged / "reports" / "unrelated.db").exists()
    assert (staged / "reports" / "allure-results" / "categories.json").is_file()
    assert "reports/traces" in copied


def test_the_path_list_pins_trust_markers_and_detector_status():
    assert "reports/run-trust*.txt" in EVIDENCE_PATHS
    assert "reports/run-trust*.json" in EVIDENCE_PATHS
    assert EVIDENCE["detector status"] == ("reports/verdicts",)


TEST_JOBS = (
    "offline",
    "browser",
    "checks",
    "visual",
    "visual-baselines",
    "drafts",
    "platform",
    "browser-matrix",
    "render",
    "bringup",
)


def _runs_pytest(job: dict) -> bool:
    return any(
        "pytest" in str(s.get("run")) or "gate.lanes bringup" in str(s.get("run"))
        for s in job["steps"]
    )


def test_every_test_job_uploads_evidence_always_and_publish_needs_them_all():
    jobs = yaml.safe_load((ROOT / ".github/workflows/tests.yml").read_text())["jobs"]
    assert {name for name, job in jobs.items() if _runs_pytest(job)} == set(TEST_JOBS)
    for name in TEST_JOBS:
        uploads = [
            s
            for s in jobs[name]["steps"]
            if str(s.get("uses", "")).startswith("actions/upload-artifact")
            and str(s["with"]["name"]).startswith(PREFIX)
        ]
        assert uploads and uploads[0].get("if") == "always()", name
    publish_job = jobs["publish"]
    assert set(publish_job["needs"]) == set(TEST_JOBS)
    assert publish_job["if"].startswith("always()")
