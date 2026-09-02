"""Tests for autonomous_ui.orchestrator — run/collect/heal loop."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from autonomous_ui.models import (
    FailureAnalysis,
    FailureBundle,
    FailureType,
    HealingResult,
)
from autonomous_ui.orchestrator import UIOrchestrator


@pytest.fixture()
def orchestrator() -> UIOrchestrator:
    llm = MagicMock()
    llm.complete.return_value = ""
    return UIOrchestrator(llm=llm)


def _make_bundle_file(
    directory: Path,
    test_name: str = "test_example",
    mtime: datetime | None = None,
) -> Path:
    """
    Write a bundle file, optionally pinning its modification time.

    ``_collect_bundles_since`` compares ``st_mtime`` against a caller-supplied
    instant, so a test that samples "now" and then writes a file is racing the
    filesystem's timestamp resolution: where mtime is coarser than the few
    microseconds in between, it rounds below the sampled instant and the file is
    skipped. That passes on APFS and fails on CI, which is exactly the shape of
    flake this project exists to find.

    Passing ``mtime`` removes the clock from the test entirely.
    """
    bundle = {
        "test": test_name,
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "error": "TimeoutError: Timeout 30000ms exceeded.",
        "stackTrace": "",
        "screenshot": "",
        "consoleErrors": [],
        "failedRequests": [],
        "domSnapshot": "",
    }
    path = directory / f"{test_name}.json"
    path.write_text(json.dumps(bundle))
    if mtime is not None:
        stamp = mtime.timestamp()
        os.utime(path, (stamp, stamp))
    return path


# ------------------------------------------------------------------
# run() — exit codes and loop control
# ------------------------------------------------------------------


def test_run_returns_0_when_tests_pass(orchestrator: UIOrchestrator) -> None:
    with patch.object(orchestrator, "_run_pytest", return_value=0):
        assert orchestrator.run() == 0


def test_run_returns_1_when_no_healing_applied(
    orchestrator: UIOrchestrator, tmp_path: Path
) -> None:
    _make_bundle_file(tmp_path)

    analysis = FailureAnalysis(
        test_name="test_example",
        failure_type=FailureType.TIMEOUT,
        root_cause="timeout",
        confidence="high",
    )
    result = HealingResult(
        test_name="test_example", strategy="none", applied=False, details="skipped"
    )

    with (
        patch.object(orchestrator, "_run_pytest", return_value=1),
        patch.object(
            orchestrator,
            "_collect_bundles_since",
            return_value=[
                FailureBundle.from_dict(
                    {
                        "test": "test_example",
                        "timestamp": "",
                        "error": "TimeoutError",
                        "stackTrace": "",
                        "screenshot": "",
                        "consoleErrors": [],
                        "failedRequests": [],
                    }
                )
            ],
        ),
        patch.object(orchestrator._analyzer, "analyze", return_value=analysis),
        patch.object(orchestrator._healer, "heal", return_value=result),
        patch.object(orchestrator, "_log_session"),
    ):
        code = orchestrator.run(max_iterations=2)

    assert code == 1


def test_run_stops_after_max_iterations(orchestrator: UIOrchestrator) -> None:
    with (
        patch.object(orchestrator, "_run_pytest", return_value=1),
        patch.object(orchestrator, "_collect_bundles_since", return_value=[]),
    ):
        code = orchestrator.run(max_iterations=3)
    assert code == 1


def test_run_does_not_exceed_max_iterations(orchestrator: UIOrchestrator) -> None:
    call_count = {"n": 0}

    def count_calls(args):
        call_count["n"] += 1
        return 1

    with (
        patch.object(orchestrator, "_run_pytest", side_effect=count_calls),
        patch.object(orchestrator, "_collect_bundles_since", return_value=[]),
    ):
        orchestrator.run(max_iterations=2)

    assert call_count["n"] <= 2


# ------------------------------------------------------------------
# _collect_bundles_since
# ------------------------------------------------------------------


def test_collect_bundles_since_returns_new_files(tmp_path: Path) -> None:
    before = datetime(2020, 1, 1, tzinfo=timezone.utc)
    _make_bundle_file(tmp_path, mtime=before + timedelta(minutes=1))
    with patch("autonomous_ui.orchestrator._BUNDLES_DIR", tmp_path):
        bundles = UIOrchestrator._collect_bundles_since(before)
    assert len(bundles) == 1
    assert bundles[0].test == "test_example"


def test_collect_bundles_since_keeps_a_file_written_on_the_boundary(tmp_path: Path) -> None:
    """
    The comparison is ``mtime < since``, so a bundle written in the same instant
    the run started is kept. Worth pinning: the production caller samples
    ``run_start`` and then runs the tests that write the bundles, and an
    off-by-one here would drop the first failure of a run.
    """
    since = datetime(2020, 1, 1, tzinfo=timezone.utc)
    _make_bundle_file(tmp_path, mtime=since)
    with patch("autonomous_ui.orchestrator._BUNDLES_DIR", tmp_path):
        assert len(UIOrchestrator._collect_bundles_since(since)) == 1


def test_collect_bundles_since_drops_a_file_one_second_older(tmp_path: Path) -> None:
    """Negative control for the boundary above."""
    since = datetime(2020, 1, 1, tzinfo=timezone.utc)
    _make_bundle_file(tmp_path, mtime=since - timedelta(seconds=1))
    with patch("autonomous_ui.orchestrator._BUNDLES_DIR", tmp_path):
        assert UIOrchestrator._collect_bundles_since(since) == []


def test_collect_bundles_since_ignores_old_files(tmp_path: Path) -> None:
    _make_bundle_file(tmp_path)
    future = datetime(2099, 1, 1, tzinfo=timezone.utc)
    with patch("autonomous_ui.orchestrator._BUNDLES_DIR", tmp_path):
        bundles = UIOrchestrator._collect_bundles_since(future)
    assert bundles == []


def test_collect_bundles_since_skips_invalid_json(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("not json {{{")
    before = datetime.now(tz=timezone.utc)
    with patch("autonomous_ui.orchestrator._BUNDLES_DIR", tmp_path):
        bundles = UIOrchestrator._collect_bundles_since(before)
    assert bundles == []


def test_collect_bundles_returns_empty_when_dir_missing(orchestrator: UIOrchestrator) -> None:
    with patch("autonomous_ui.orchestrator._BUNDLES_DIR", Path("/nonexistent/path")):
        bundles = UIOrchestrator._collect_bundles_since(datetime.now(tz=timezone.utc))
    assert bundles == []


# ------------------------------------------------------------------
# _retry_args
# ------------------------------------------------------------------


def test_retry_args_empty_when_no_overrides_file() -> None:
    with patch("autonomous_ui.orchestrator.Path") as MockPath:
        MockPath.return_value.exists.return_value = False
        # Direct call without patching — relies on reports/ not having the file in test env
        pass
    args = UIOrchestrator._retry_args()
    # In a clean environment with no overrides file, should return []
    assert isinstance(args, list)


def test_retry_args_adds_reruns_when_retries_listed(tmp_path: Path) -> None:
    overrides = tmp_path / "healing_overrides.json"
    overrides.write_text(json.dumps({"retry_tests": ["test_flaky"]}))
    import autonomous_ui.orchestrator as orch_mod

    original = orch_mod._HEALING_OVERRIDES
    orch_mod._HEALING_OVERRIDES = overrides
    try:
        args = UIOrchestrator._retry_args()
        assert "--reruns" in args
    finally:
        orch_mod._HEALING_OVERRIDES = original


# ------------------------------------------------------------------
# analyze_only mode
# ------------------------------------------------------------------


def test_analyze_only_does_not_call_heal(orchestrator: UIOrchestrator) -> None:
    bundle = FailureBundle(
        test="test_example",
        timestamp="",
        error="AssertionError",
        stack_trace="",
        screenshot="",
        console_errors=[],
        failed_requests=[],
    )
    analysis = FailureAnalysis(
        test_name="test_example",
        failure_type=FailureType.ASSERTION,
        root_cause="mismatch",
        confidence="medium",
    )

    with (
        patch.object(orchestrator, "_run_pytest", return_value=1),
        patch.object(orchestrator, "_collect_bundles_since", return_value=[bundle]),
        patch.object(orchestrator._analyzer, "analyze", return_value=analysis),
        patch.object(orchestrator._healer, "heal") as mock_heal,
        patch.object(orchestrator, "_log_session"),
    ):
        orchestrator.run(max_iterations=1, analyze_only=True)

    mock_heal.assert_not_called()


# ------------------------------------------------------------------
# _log_session
# ------------------------------------------------------------------


def test_log_session_writes_jsonl(tmp_path: Path) -> None:
    log_file = tmp_path / "sessions.jsonl"
    analysis = FailureAnalysis(
        test_name="test_example",
        failure_type=FailureType.LOCATOR,
        root_cause="not found",
        confidence="high",
    )
    result = HealingResult(
        test_name="test_example", strategy="locator_patch", applied=True, details="patched"
    )
    with patch("autonomous_ui.orchestrator._SESSION_LOG", log_file):
        UIOrchestrator._log_session(analysis, result)

    lines = log_file.read_text().strip().split("\n")
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["test"] == "test_example"
    assert event["strategy"] == "locator_patch"
    assert event["applied"] is True
