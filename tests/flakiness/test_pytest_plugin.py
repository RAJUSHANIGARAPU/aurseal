"""Tests for autonomous_ui.flakiness.pytest_plugin — what gets recorded, and once.

These build report and config objects directly. Nothing here starts pytest,
spawns an xdist worker, sleeps, or reaches a network: a recording bug has to be
provable from the hook input alone, or the test is measuring the harness.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from autonomous_ui.flakiness.history_store import HistoryStore
from autonomous_ui.flakiness.pattern_analyzer import PatternAnalyzer
from autonomous_ui.flakiness.pytest_plugin import FlakinessPlugin


class _Report:
    """The parts of a pytest TestReport the plugin reads."""

    def __init__(
        self,
        nodeid: str = "ui/tests/test_search.py::test_search",
        outcome: str = "passed",
        when: str = "call",
        longrepr: str = "",
        duration: float = 1.0,
    ) -> None:
        self.nodeid = nodeid
        self.outcome = outcome
        self.when = when
        self.longrepr = longrepr
        self.duration = duration

    # pytest derives these three from `outcome`; a "rerun" report is none of them.
    @property
    def passed(self) -> bool:
        return self.outcome == "passed"

    @property
    def failed(self) -> bool:
        return self.outcome == "failed"

    @property
    def skipped(self) -> bool:
        return self.outcome == "skipped"


def _config(dist: str = "no", workerid: str | None = None) -> SimpleNamespace:
    cfg = SimpleNamespace(option=SimpleNamespace(dist=dist))
    cfg.getoption = lambda name, default=None: default  # noqa: ARG005
    if workerid is not None:
        cfg.workerinput = {"workerid": workerid}
    return cfg


@pytest.fixture()
def store(tmp_path: Path) -> HistoryStore:
    return HistoryStore(store_path=tmp_path / "history.jsonl")


# ------------------------------------------------------------------
# A rerun is not a skip
# ------------------------------------------------------------------


def test_rerun_report_is_recorded_as_rerun(store: HistoryStore) -> None:
    plugin = FlakinessPlugin.from_config(_config(), store=store)
    plugin.pytest_runtest_logreport(_Report(outcome="rerun", longrepr="TimeoutError"))

    outcomes = [r.outcome for r in store.load_all()]
    assert outcomes == ["rerun"]


def test_rerun_report_keeps_its_error_text(store: HistoryStore) -> None:
    # The failed attempt is the only place the flake's error text exists — the
    # passing rerun that follows carries none. Dropping it blinds PatternAnalyzer.
    plugin = FlakinessPlugin.from_config(_config(), store=store)
    plugin.pytest_runtest_logreport(_Report(outcome="rerun", longrepr="TimeoutError: 30000ms"))

    assert "TimeoutError" in store.load_all()[0].error


def test_passed_failed_skipped_still_recorded_verbatim(store: HistoryStore) -> None:
    # Positive control: fixing the rerun case must not disturb the three
    # outcomes that were already right.
    plugin = FlakinessPlugin.from_config(_config(), store=store)
    for outcome in ("passed", "failed", "skipped"):
        plugin.pytest_runtest_logreport(_Report(outcome=outcome))

    assert [r.outcome for r in store.load_all()] == ["passed", "failed", "skipped"]


def test_non_call_phase_is_ignored(store: HistoryStore) -> None:
    plugin = FlakinessPlugin.from_config(_config(), store=store)
    plugin.pytest_runtest_logreport(_Report(outcome="passed", when="setup"))
    plugin.pytest_runtest_logreport(_Report(outcome="passed", when="teardown"))

    assert store.load_all() == []


# ------------------------------------------------------------------
# One record per outcome under xdist
# ------------------------------------------------------------------


def test_xdist_controller_does_not_record_replayed_worker_reports(store: HistoryStore) -> None:
    # Measured on the unfixed plugin: 6 tests with `-n 2` produced 12 records
    # (main:6, gw0:3, gw1:3), because the controller replays every worker's
    # logreport as its own.
    controller = FlakinessPlugin.from_config(_config(dist="load"), store=store)
    for i in range(3):
        controller.pytest_runtest_logreport(_Report(nodeid=f"t::test_{i}"))

    assert store.load_all() == []


def test_xdist_worker_records_under_its_own_worker_id(store: HistoryStore) -> None:
    # Positive control for the rule above: suppressing the controller must not
    # suppress the workers, or the history goes empty under `-n`.
    gw0 = FlakinessPlugin.from_config(_config(dist="load", workerid="gw0"), store=store)
    gw1 = FlakinessPlugin.from_config(_config(dist="load", workerid="gw1"), store=store)
    gw0.pytest_runtest_logreport(_Report(nodeid="t::test_a"))
    gw1.pytest_runtest_logreport(_Report(nodeid="t::test_b"))

    assert sorted(r.worker for r in store.load_all()) == ["gw0", "gw1"]


def test_sequential_run_still_records(store: HistoryStore) -> None:
    # Positive control: without xdist the controller IS the executor.
    plugin = FlakinessPlugin.from_config(_config(dist="no"), store=store)
    plugin.pytest_runtest_logreport(_Report())

    records = store.load_all()
    assert len(records) == 1
    assert records[0].worker == "main"


def test_no_dist_attribute_still_records(store: HistoryStore) -> None:
    # xdist not installed: config.option has no `dist` at all.
    cfg = SimpleNamespace(option=SimpleNamespace())
    cfg.getoption = lambda name, default=None: default  # noqa: ARG005
    plugin = FlakinessPlugin.from_config(cfg, store=store)
    plugin.pytest_runtest_logreport(_Report())

    assert len(store.load_all()) == 1


# ------------------------------------------------------------------
# Session end
# ------------------------------------------------------------------


def test_worker_does_not_write_a_report(
    tmp_path: Path, store: HistoryStore, monkeypatch, capsys
) -> None:
    # Every worker running sessionfinish means N report pairs per invocation.
    monkeypatch.chdir(tmp_path)
    gw0 = FlakinessPlugin.from_config(_config(dist="load", workerid="gw0"), store=store)
    for i in range(10):
        gw0.pytest_runtest_logreport(
            _Report(nodeid="t::test_a", outcome="failed" if i else "passed")
        )
    gw0.pytest_sessionfinish(session=None, exitstatus=0)

    assert capsys.readouterr().out == ""
    assert not (tmp_path / "reports").exists()


def test_session_end_prunes_history(tmp_path: Path) -> None:
    store = HistoryStore(store_path=tmp_path / "history.jsonl", max_records=4)
    plugin = FlakinessPlugin.from_config(_config(), store=store)
    for i in range(10):
        plugin.pytest_runtest_logreport(_Report(nodeid=f"t::test_{i}"))
    plugin.pytest_sessionfinish(session=None, exitstatus=0)

    assert len(store.load_all()) == 4


def test_worker_only_records_keep_resource_contention_detectable(store: HistoryStore) -> None:
    # The mirroring did not just double the counts — it made
    # PatternAnalyzer._is_parallel_contention dead code for xdist history. Every
    # parallel record had a "main" twin with the SAME outcome, so parallel_rate
    # always equalled sequential_rate and `>= 3 *` could never hold.
    sequential = FlakinessPlugin.from_config(_config(dist="no"), store=store)
    for _ in range(4):
        sequential.pytest_runtest_logreport(_Report(nodeid="t::test_a", outcome="passed"))

    gw0 = FlakinessPlugin.from_config(_config(dist="load", workerid="gw0"), store=store)
    controller = FlakinessPlugin.from_config(_config(dist="load"), store=store)
    for _ in range(4):
        report = _Report(nodeid="t::test_a", outcome="failed", longrepr="boom")
        gw0.pytest_runtest_logreport(report)
        controller.pytest_runtest_logreport(report)  # the replay

    records = store.load_for_test("t::test_a")
    assert PatternAnalyzer()._is_parallel_contention(records) is True


def test_worker_identity_comes_from_the_config_not_the_environment(monkeypatch) -> None:
    """
    This project's own suite runs under `-n auto` in CI, which sets
    PYTEST_XDIST_WORKER in the process executing this very test. Reading it as a
    fallback let the outer session's identity leak into a plugin built from a
    config that declares no distribution — a sequential run tagged "gw3", which
    made two tests here pass locally and fail in CI.

    `workerinput` is what xdist sets on a worker, and it is the only thing that
    should decide.
    """
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw7")
    plugin = FlakinessPlugin.from_config(_config(dist="no"))
    assert plugin._worker == "main"


def test_a_real_worker_still_reports_its_id(monkeypatch) -> None:
    """Control: removing the env fallback must not lose the genuine id."""
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    plugin = FlakinessPlugin.from_config(_config(dist="load", workerid="gw2"))
    assert plugin._worker == "gw2"
