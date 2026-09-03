"""What an orchestration run is allowed to call a success, and what it heals.

Nothing here generates, executes or reaches the network: the ingestion,
generation, execution and healing agents the Orchestrator builds are all
replaced (``_fakes.install_orchestrator_fakes``). The one exception is the
stale-report test, which drives the REAL ExecutionAgent over a stubbed
``subprocess.run`` because that defect only exists at the seam between them.

Three things were wrong, and two of them point in opposite directions — a fix
for either one alone makes the other worse, so the controls matter:

* a crashed run reported the previous run's ``passed: 7`` as ``success=True``;
* a run that collected nothing reported ``success=True`` on all-zero counts;
* an all-green run with ``stop_on_success=False`` fell through to the
  exhausted branch, which hardcoded ``success=False``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from api.agents.execution import ExecutionResult
from api.agents.orchestrator import Orchestrator

from ._fakes import (
    FakeExecution,
    FakeHealing,
    FakePytest,
    install_orchestrator_fakes,
    report_payload,
)


def _orchestrator(tmp_path: Path, **kwargs) -> Orchestrator:
    return Orchestrator(
        collection_path=tmp_path / "collection.json",
        output_dir=tmp_path / "gen",
        **kwargs,
    )


# --- success is only for runs that produced a verdict ---


def test_crashed_run_does_not_report_the_previous_runs_success(monkeypatch, tmp_path: Path) -> None:
    """The end-to-end probe: report says 7 passed, this run exited 4 having run nothing."""
    report_file = tmp_path / "reports" / "pytest_report.json"
    report_file.parent.mkdir(parents=True)
    report_file.write_text(json.dumps(report_payload(passed=7)), encoding="utf-8")
    monkeypatch.setattr("api.agents.execution._REPORT_FILE", report_file)
    FakePytest(returncode=4, report=None, stderr="ERROR: unrecognized arguments\n").install(
        monkeypatch
    )
    install_orchestrator_fakes(
        monkeypatch,
        generated=[tmp_path / "gen" / "test_a.py"],
        execution=None,  # the real ExecutionAgent — this defect lives at that seam
    )

    result = _orchestrator(tmp_path, max_retries=1).run()

    assert not result.success
    assert result.final_pass_count == 0, "7 passes belonged to the previous run"


def test_no_tests_collected_is_not_a_success(monkeypatch, tmp_path: Path) -> None:
    install_orchestrator_fakes(
        monkeypatch,
        generated=[tmp_path / "test_a.py"],
        execution=FakeExecution(
            ExecutionResult(exit_code=5, error="pytest collected no tests (exit 5)")
        ),
    )

    result = _orchestrator(tmp_path, max_retries=1).run()

    assert not result.success
    assert result.error


def test_a_no_verdict_run_stops_immediately_rather_than_healing(
    monkeypatch, tmp_path: Path
) -> None:
    """Healing cannot fix a usage error, and four goes at it costs four timeouts."""
    execution = FakeExecution(ExecutionResult(exit_code=4, error="pytest usage error (exit 4)"))
    healing = install_orchestrator_fakes(
        monkeypatch, generated=[tmp_path / "test_a.py"], execution=execution
    )

    result = _orchestrator(tmp_path, max_retries=3).run()

    assert not result.success
    assert execution.runs == 1
    assert healing.calls == []


# --- an all-green run is a success on every path ---


def test_all_green_is_a_success_when_stopping_on_success(monkeypatch, tmp_path: Path) -> None:
    install_orchestrator_fakes(
        monkeypatch,
        generated=[tmp_path / "test_a.py"],
        execution=FakeExecution(ExecutionResult(passed=5, exit_code=0)),
    )

    result = _orchestrator(tmp_path, max_retries=3, stop_on_success=True).run()

    assert result.success
    assert result.final_pass_count == 5
    assert result.final_fail_count == 0
    assert result.total_runs == 1


def test_all_green_is_a_success_when_not_stopping_on_success(monkeypatch, tmp_path: Path) -> None:
    """stop_on_success=False is a documented option, not a way to fail a clean suite."""
    install_orchestrator_fakes(
        monkeypatch,
        generated=[tmp_path / "test_a.py"],
        execution=FakeExecution(ExecutionResult(passed=5, exit_code=0)),
    )

    result = _orchestrator(tmp_path, max_retries=2, stop_on_success=False).run()

    assert result.success
    assert result.final_pass_count == 5
    assert result.final_fail_count == 0


def test_run_that_ends_red_is_still_a_failure(monkeypatch, tmp_path: Path) -> None:
    """The control for the two above: a genuine failure must survive the fix."""
    install_orchestrator_fakes(
        monkeypatch,
        generated=[tmp_path / "test_a.py"],
        execution=FakeExecution(ExecutionResult(passed=1, failed=2, exit_code=1)),
    )

    result = _orchestrator(tmp_path, max_retries=1).run()

    assert not result.success
    assert result.final_fail_count == 2


def test_healing_a_suite_green_reports_success(monkeypatch, tmp_path: Path) -> None:
    """Red first, green after one heal — the loop's whole purpose."""
    install_orchestrator_fakes(
        monkeypatch,
        generated=[tmp_path / "test_a.py"],
        execution=FakeExecution(
            ExecutionResult(passed=1, failed=1, exit_code=1),
            ExecutionResult(passed=2, exit_code=0),
        ),
    )

    result = _orchestrator(tmp_path, max_retries=3).run()

    assert result.success
    assert result.total_runs == 2


def test_retry_budget_is_unchanged(monkeypatch, tmp_path: Path) -> None:
    """max_retries=3 means one initial run plus three retries."""
    execution = FakeExecution(ExecutionResult(passed=0, failed=1, exit_code=1))
    install_orchestrator_fakes(monkeypatch, generated=[tmp_path / "test_a.py"], execution=execution)

    result = _orchestrator(tmp_path, max_retries=3).run()

    assert execution.runs == 4
    assert result.total_runs == 4


# --- a file is healed only with its own failures ---


@pytest.fixture
def two_files(tmp_path: Path) -> list[Path]:
    gen = tmp_path / "gen"
    gen.mkdir()
    for name in ("test_users.py", "test_orders.py"):
        (gen / name).write_text("def test_x():\n    assert True\n", encoding="utf-8")
    return [gen / "test_users.py", gen / "test_orders.py"]


def _run_with_failures(
    monkeypatch, tmp_path: Path, files: list[Path], details: list[dict]
) -> FakeHealing:
    execution = FakeExecution(
        ExecutionResult(passed=1, failed=len(details), exit_code=1, failure_details=details)
    )
    healing = install_orchestrator_fakes(monkeypatch, generated=files, execution=execution)
    _orchestrator(tmp_path, max_retries=1).run()
    return healing


def test_a_file_with_no_failures_is_not_healed(
    monkeypatch, tmp_path: Path, two_files: list[Path]
) -> None:
    healing = _run_with_failures(
        monkeypatch,
        tmp_path,
        two_files,
        [{"test_name": "gen/test_users.py::test_list", "message": "ReadTimeout"}],
    )

    assert healing.names_for("test_orders.py") == []
    assert healing.names_for("test_users.py") == ["gen/test_users.py::test_list"]


def test_substring_neighbour_does_not_cross_fire(
    monkeypatch, tmp_path: Path, two_files: list[Path]
) -> None:
    """``test_users`` is a substring of ``test_users_admin.py`` — the old match hit both."""
    healing = _run_with_failures(
        monkeypatch,
        tmp_path,
        two_files,
        [{"test_name": "gen/test_users_admin.py::test_list", "message": "ReadTimeout"}],
    )

    assert healing.names_for("test_users.py") == []


def test_fabricated_unknown_names_heal_nothing(
    monkeypatch, tmp_path: Path, two_files: list[Path]
) -> None:
    """AnalysisAgent invents ``unknown_test_N`` when it has counts but no detail.

    Those name no file, and the old fallback therefore rewrote every file from
    them.
    """
    execution = FakeExecution(
        ExecutionResult(passed=0, failed=2, exit_code=1, raw_output="something broke")
    )
    healing = install_orchestrator_fakes(monkeypatch, generated=two_files, execution=execution)

    _orchestrator(tmp_path, max_retries=1).run()

    assert healing.calls == []


def test_matching_failures_still_reach_their_file(
    monkeypatch, tmp_path: Path, two_files: list[Path]
) -> None:
    """The control: attribution must not become so strict that nothing is healed."""
    healing = _run_with_failures(
        monkeypatch,
        tmp_path,
        two_files,
        [
            {"test_name": "gen/test_users.py::test_list", "message": "ReadTimeout"},
            {"test_name": "gen/test_orders.py::test_create", "message": "ReadTimeout"},
        ],
    )

    assert healing.names_for("test_users.py") == ["gen/test_users.py::test_list"]
    assert healing.names_for("test_orders.py") == ["gen/test_orders.py::test_create"]


def test_absolute_nodeid_matches_its_file(
    monkeypatch, tmp_path: Path, two_files: list[Path]
) -> None:
    healing = _run_with_failures(
        monkeypatch,
        tmp_path,
        two_files,
        [{"test_name": f"{two_files[0]}::test_list", "message": "ReadTimeout"}],
    )

    assert healing.names_for("test_users.py") == [f"{two_files[0]}::test_list"]
    assert healing.names_for("test_orders.py") == []
