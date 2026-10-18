"""The lane runner: command line, JUnit summary, exit codes, and one real lane run."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from scripts.run_lane import ClassResult, build_command, main, render, summarise

REPORT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="6">
  <testcase classname="lane.test_create.TestCreate" name="test_a"/>
  <testcase classname="lane.test_create.TestCreate" name="test_b"/>
  <testcase classname="lane.test_renew" name="test_due">
    <failure message="assert 1 == 2">boom</failure></testcase>
  <testcase classname="lane.test_renew" name="test_not_due"/>
  <testcase classname="lane.test_cancel" name="test_x"><skipped message="no data"/></testcase>
  <testcase classname="lane.test_change" name="test_y"><error message="setup"/></testcase>
</testsuite></testsuites>
"""


def test_summary_counts_per_class(tmp_path):
    xml = tmp_path / "lane.xml"
    xml.write_text(REPORT)
    assert summarise(xml) == [
        ClassResult("lane.test_create.TestCreate", 2, 0, 0),
        ClassResult("lane.test_renew", 1, 1, 0),
        ClassResult("lane.test_cancel", 0, 0, 1),
        ClassResult("lane.test_change", 0, 1, 0),
    ]


def test_status_per_class_and_totals_line(tmp_path):
    xml = tmp_path / "lane.xml"
    xml.write_text(REPORT)
    lines = render(summarise(xml)).splitlines()
    assert [line.split()[0] for line in lines] == ["PASS", "FAIL", "SKIP", "FAIL", "FAIL"]
    assert lines[-1].split()[1:] == ["total", "3", "passed,", "2", "failed,", "1", "skipped"]
    assert render([]) == "no tests ran in this lane"


def test_command_carries_marker_paths_report_and_passthrough(tmp_path):
    junit = tmp_path / "x.xml"
    cmd = build_command("lifecycle", ["tests/a"], junit, ["-n", "2"])
    assert cmd[:3] == [sys.executable, "-m", "pytest"]
    assert cmd[3:] == ["tests/a", "-m", "lifecycle", f"--junitxml={junit}", "-n", "2"]
    assert "-m" not in build_command(None, ["tests/a"], junit)[3:]


def test_main_needs_a_selection():
    with pytest.raises(SystemExit):
        main([], runner=lambda cmd: 0, environ={})


def test_main_prints_summary_and_returns_pytest_code(tmp_path, capsys):
    def runner(cmd):
        report = Path(next(a for a in cmd if a.startswith("--junitxml=")).split("=", 1)[1])
        report.write_text(REPORT)
        assert cmd[-2:] == ["-x", "-q"]
        return 1

    code = main(
        ["-m", "lifecycle or renewal", "--", "-x", "-q"],
        runner=runner,
        environ={"LANE_REPORT_DIR": str(tmp_path)},
    )
    out = capsys.readouterr().out
    assert code == 1
    assert "FAIL  lane.test_renew" in out
    assert (tmp_path / "lifecycle_or_renewal.xml").exists()


def test_stale_report_is_never_summarised(tmp_path, capsys):
    (tmp_path / "lifecycle.xml").write_text(REPORT)
    code = main(
        ["-m", "lifecycle"], runner=lambda cmd: 0, environ={"LANE_REPORT_DIR": str(tmp_path)}
    )
    assert code == 3
    assert "missing or unreadable" in capsys.readouterr().err


def test_real_lane_run(tmp_path, capsys):
    lane = tmp_path / "lane"
    lane.mkdir()
    (lane / "test_sample.py").write_text(
        textwrap.dedent(
            """
            import pytest

            class TestCreate:
                def test_ok(self):
                    assert True

            def test_skipped():
                pytest.skip("no data")
            """
        )
    )
    reports = tmp_path / "reports"
    code = main(
        [str(lane), "--name", "sample", "--", "-p", "no:cacheprovider", "-q"],
        runner=lambda cmd: subprocess.call(cmd, cwd=tmp_path),
        environ={"LANE_REPORT_DIR": str(reports)},
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "PASS  lane.test_sample.TestCreate" in out
    assert "SKIP  lane.test_sample " in out
