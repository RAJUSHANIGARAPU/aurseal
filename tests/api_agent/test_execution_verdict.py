"""A pytest run that produced no verdict must not be scored as a green one.

Every test here stubs ``subprocess.run`` — no real pytest, no network.

The four shapes that used to be indistinguishable from "everything passed":

1. pytest crashed before the JSON plugin wrote anything, and the report from
   the PREVIOUS run was still on disk. Measured: a report saying ``passed: 7``,
   a run exiting 4 with zero tests executed, parsed as ``passed=7, failed=0``.
2. no report at all and no summary line in the output — parsed as
   ``passed=0, failed=0, errors=0``, which reads as clean.
3. exit 5, "no tests collected".
4. an empty file list, which returned a bare ``ExecutionResult()``.

The positive controls at the bottom are not decoration: a parser hardwired to
return "no verdict" would satisfy all four cases above.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from api.agents.execution import ExecutionAgent

from ._fakes import FakePytest, failed_test_entry, report_payload


@pytest.fixture
def report_file(tmp_path: Path) -> Path:
    return tmp_path / "reports" / "pytest_report.json"


@pytest.fixture
def test_files(tmp_path: Path) -> list[Path]:
    path = tmp_path / "test_generated.py"
    path.write_text("def test_x():\n    assert True\n", encoding="utf-8")
    return [path]


def _write_stale(report_file: Path, passed: int = 7) -> None:
    report_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.write_text(json.dumps(report_payload(passed=passed)), encoding="utf-8")


# --- 1. the stale report ---


def test_crashed_run_does_not_report_the_previous_runs_numbers(
    monkeypatch, report_file: Path, test_files: list[Path]
) -> None:
    _write_stale(report_file, passed=7)
    FakePytest(returncode=4, report=None, stderr="ERROR: unrecognized arguments\n").install(
        monkeypatch
    )

    result = ExecutionAgent(report_file=report_file).run(test_files)

    assert result.passed == 0, "the 7 passes belonged to the previous run"
    assert not result.ran
    assert result.exit_code == 4


def test_stale_report_is_deleted_before_the_run(
    monkeypatch, report_file: Path, test_files: list[Path]
) -> None:
    """Not merely ignored — gone, so nothing downstream can pick it up either."""
    _write_stale(report_file, passed=7)
    FakePytest(returncode=4, report=None).install(monkeypatch)

    ExecutionAgent(report_file=report_file).run(test_files)

    assert not report_file.exists()


def test_exit_one_with_a_report_showing_no_failures_is_not_a_verdict(
    monkeypatch, report_file: Path, test_files: list[Path]
) -> None:
    """pytest said something failed; the report disagrees. Believe neither."""
    FakePytest(returncode=1, report=report_payload(passed=3)).install(monkeypatch)

    result = ExecutionAgent(report_file=report_file).run(test_files)

    assert not result.ran


# --- 2 and 3. no report, nothing collected ---


def test_no_report_and_no_summary_is_not_a_verdict(
    monkeypatch, report_file: Path, test_files: list[Path]
) -> None:
    FakePytest(returncode=4, report=None, stdout="ERROR: file or directory not found\n").install(
        monkeypatch
    )

    result = ExecutionAgent(report_file=report_file).run(test_files)

    assert not result.ran
    assert result.passed == 0


def test_exit_five_no_tests_collected_is_not_a_verdict(
    monkeypatch, report_file: Path, test_files: list[Path]
) -> None:
    FakePytest(returncode=5, report=None, stdout="no tests ran in 0.01s\n").install(monkeypatch)

    result = ExecutionAgent(report_file=report_file).run(test_files)

    assert not result.ran
    assert "collected no tests" in result.error


def test_exit_zero_without_a_report_is_not_a_verdict(
    monkeypatch, report_file: Path, test_files: list[Path]
) -> None:
    """The json plugin failing to load is exactly how defect 1 started."""
    FakePytest(returncode=0, report=None, stdout="collected 0 items\n").install(monkeypatch)

    result = ExecutionAgent(report_file=report_file).run(test_files)

    assert not result.ran


# --- 4. empty input ---


def test_empty_file_list_is_not_a_verdict(monkeypatch, report_file: Path) -> None:
    fake = FakePytest(returncode=0).install(monkeypatch)

    result = ExecutionAgent(report_file=report_file).run([])

    assert not result.ran
    assert fake.pytest_calls == [], "nothing should have been executed"


# --- 5. the subprocess is bounded ---


def test_pytest_run_is_given_a_timeout(
    monkeypatch, report_file: Path, test_files: list[Path]
) -> None:
    fake = FakePytest(returncode=0, report=report_payload(passed=1)).install(monkeypatch)

    ExecutionAgent(report_file=report_file).run(test_files)

    timeout = fake.pytest_calls[0]["kwargs"].get("timeout")
    assert timeout is not None and timeout > 0


def test_a_hanging_run_becomes_a_no_verdict_not_an_exception(
    monkeypatch, report_file: Path, test_files: list[Path]
) -> None:
    FakePytest(returncode=0, raises=subprocess.TimeoutExpired(cmd="pytest", timeout=1.0)).install(
        monkeypatch
    )

    result = ExecutionAgent(report_file=report_file, timeout=1.0).run(test_files)

    assert not result.ran
    assert "timed out" in result.error


def test_raw_output_is_capped(monkeypatch, report_file: Path, test_files: list[Path]) -> None:
    huge = "x" * 2_000_000
    FakePytest(returncode=0, report=report_payload(passed=1), stdout=huge).install(monkeypatch)

    result = ExecutionAgent(report_file=report_file).run(test_files)

    assert len(result.raw_output) < len(huge)


# --- positive controls: a real verdict is still read, and still trusted ---


def test_green_run_reports_its_passes(
    monkeypatch, report_file: Path, test_files: list[Path]
) -> None:
    FakePytest(returncode=0, report=report_payload(passed=3, duration=1.5)).install(monkeypatch)

    result = ExecutionAgent(report_file=report_file).run(test_files)

    assert result.ran
    assert (result.passed, result.failed, result.errors) == (3, 0, 0)
    assert result.exit_code == 0
    assert result.duration == 1.5


def test_failing_run_reports_its_failures_with_detail(
    monkeypatch, report_file: Path, test_files: list[Path]
) -> None:
    payload = report_payload(
        passed=2,
        failed=1,
        tests=[failed_test_entry("gen/test_a.py::test_boom", "AssertionError: 404 != 200")],
    )
    FakePytest(returncode=1, report=payload).install(monkeypatch)

    result = ExecutionAgent(report_file=report_file).run(test_files)

    assert result.ran
    assert (result.passed, result.failed) == (2, 1)
    assert result.failure_details[0]["test_name"] == "gen/test_a.py::test_boom"


def test_green_run_after_a_stale_report_reports_only_the_new_numbers(
    monkeypatch, report_file: Path, test_files: list[Path]
) -> None:
    """The control for the deletion: it must not cost a genuine run its result."""
    _write_stale(report_file, passed=7)
    FakePytest(returncode=0, report=report_payload(passed=2)).install(monkeypatch)

    result = ExecutionAgent(report_file=report_file).run(test_files)

    assert result.ran
    assert result.passed == 2
