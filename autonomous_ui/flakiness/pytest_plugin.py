"""Pytest plugin that silently records every test outcome for flakiness analysis.

Registration (in root conftest.py pytest_configure):
    from autonomous_ui.flakiness.pytest_plugin import FlakinessPlugin
    config.pluginmanager.register(FlakinessPlugin.from_config(config), "flakiness-tracker")

The plugin adds zero latency to tests — the JSONL write is fire-and-forget.
It also generates a flakiness report at the end of every session with enough history.

That report is rule-based and offline. Set ENABLE_FLAKINESS_LLM=true to have
each flaky test's suggestion enriched by a model call, which is a network round
trip (or a `claude -p` subprocess) per flaky test and is why it is not the
default.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from autonomous_ui.flakiness.detector import FlakinessDetector
from autonomous_ui.flakiness.history_store import HistoryStore
from autonomous_ui.flakiness.models import FlakRecord
from autonomous_ui.flakiness.pattern_analyzer import PatternAnalyzer
from autonomous_ui.flakiness.remediator import FlakinessRemediator
from autonomous_ui.flakiness.reporter import FlakinessReporter


def _llm_enabled() -> bool:
    """
    Whether the session-end report may call a language model.

    Off by default, and opt-in through an environment variable like every other
    optional subsystem this project registers (ENABLE_MEMORY,
    ENABLE_CONTRACT_TESTING, ENABLE_WEB_DISCOVERY, ENABLE_GRAPHIFY).

    It was previously always on, with no switch and nothing in the docstring
    saying so. ``ClaudeLLMClient`` posts to the Anthropic API when a key is set
    and otherwise shells out to ``claude -p`` with a 120-second timeout — once
    per flaky test, synchronously, at the end of every pytest invocation. On a
    checkout with a little history that added ~23 seconds per flaky test to
    every run, including runs of a single unrelated unit test, while the module
    docstring advertised "zero latency". CI never saw it: a fresh checkout has
    no history, so no profiles exist and the report returns early.
    """
    return os.getenv("ENABLE_FLAKINESS_LLM", "false").lower() in ("1", "true", "yes")


# Outcomes worth an error message. "rerun" is pytest-rerunfailures' outcome for
# an attempt it will retry — and on a test that then passes, that attempt is the
# ONLY place the failure text exists. Dropping it left PatternAnalyzer with an
# empty error corpus for precisely the flaky tests it is meant to classify.
_OUTCOMES_WITH_ERROR = ("failed", "rerun")


class FlakinessPlugin:
    """Records test outcomes and emits a flakiness report at session end."""

    def __init__(
        self,
        store: HistoryStore,
        run_id: str,
        environment: str,
        worker: str = "main",
        record_enabled: bool = True,
        report_enabled: bool = True,
    ) -> None:
        self._store = store
        self._run_id = run_id
        self._environment = environment
        self._worker = worker
        self._record_enabled = record_enabled
        self._report_enabled = report_enabled

    @classmethod
    def from_config(cls, config, store: HistoryStore | None = None) -> FlakinessPlugin:
        run_id = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        try:
            env = config.getoption("--env", default="qa")
        except (ValueError, AttributeError):
            env = "qa"

        # `workerinput` is set by xdist on worker processes only, so its presence
        # is what separates a worker from the controller.
        worker_input = getattr(config, "workerinput", None)
        is_worker = worker_input is not None
        distributing = getattr(getattr(config, "option", None), "dist", "no") not in (None, "no")

        # Under xdist the controller REPLAYS every worker's logreport as its own,
        # so recording on both sides stores every outcome twice. Measured: 6
        # tests with `-n 2` produced 12 records (main:6, gw0:3, gw1:3), which
        # doubles total_runs and max_consecutive_failures and reaches MIN_RUNS in
        # half the real runs. It also made PatternAnalyzer's contention rule dead
        # code: every parallel record had a mirrored "main" record with the same
        # outcome, so parallel_rate could never reach 3x sequential_rate and
        # RESOURCE_CONTENTION was unreachable for xdist history.
        #
        # The workers are the ones that actually execute, so they record and the
        # controller stays quiet. Reporting is the other way round: the
        # controller reads the shared file once at the end, instead of every
        # worker writing its own report pair.
        return cls(
            store=store or HistoryStore(),
            run_id=run_id,
            environment=env,
            # Taken from `workerinput` alone. Reading PYTEST_XDIST_WORKER as a
            # fallback let the surrounding session's identity leak in: when this
            # project's own suite runs under `-n auto`, that variable is set in
            # the worker executing the test, so a config declaring dist="no" was
            # still tagged "gw3" and a sequential run looked parallel. xdist
            # always sets `workerinput` on a worker, so the variable was a second
            # source of truth that could only disagree.
            worker=(worker_input or {}).get("workerid", "main"),
            record_enabled=is_worker or not distributing,
            report_enabled=not is_worker,
        )

    # ------------------------------------------------------------------
    # pytest hooks
    # ------------------------------------------------------------------

    def pytest_runtest_logreport(self, report) -> None:
        if report.when != "call" or not self._record_enabled:
            return

        # Taken from report.outcome, never inferred from passed/failed. A
        # "rerun" report is neither: report.passed and report.failed are both
        # False, so the old ternary fell through to "skipped" and inverted the
        # whole signal. A genuinely flaky test recorded ['skipped', 'passed'],
        # scored failure_count 0, and was never flagged flaky — while a test
        # failing all three attempts recorded ['skipped', 'skipped', 'failed'],
        # scored rate 0.33 and WAS.
        outcome = getattr(report, "outcome", "") or "skipped"
        error = str(report.longrepr)[:2000] if outcome in _OUTCOMES_WITH_ERROR else ""

        self._store.record(
            FlakRecord(
                test_id=report.nodeid,
                run_id=self._run_id,
                outcome=outcome,
                duration_s=getattr(report, "duration", 0.0),
                error=error,
                timestamp=datetime.now(tz=timezone.utc).isoformat(),
                worker=self._worker,
                environment=self._environment,
            )
        )

    def pytest_sessionfinish(self, session, exitstatus) -> None:  # noqa: ARG002
        """Bound the history file, then report if there is enough of it."""
        if not self._report_enabled:
            return

        # Before the early return below, not after: a checkout where nothing is
        # flaky enough to profile is exactly the one that would never rotate.
        self._store.prune()

        groups = self._store.grouped_by_test()
        detector = FlakinessDetector(self._store)
        profiles = detector.get_profiles(groups=groups)
        if not profiles:
            return

        analyzer = PatternAnalyzer()
        remediator = FlakinessRemediator()
        analyses = {}
        for profile in profiles:
            if not profile.is_flaky:
                continue
            records = groups.get(profile.test_id, [])
            pattern = analyzer.classify(profile, records, use_llm=False)
            remediation = remediator.remediate(profile, pattern, records, use_llm=_llm_enabled())
            analyses[profile.test_id] = (pattern, remediation)

        flaky_count = sum(1 for p in profiles if p.is_flaky)
        if flaky_count > 0:
            reporter = FlakinessReporter()
            md_path, json_path = reporter.write(profiles, analyses)
            print(f"\n[flakiness] {flaky_count} flaky test(s) detected." f" Report: {md_path}")
        else:
            print(f"\n[flakiness] {len(profiles)} test(s) tracked — no flakiness detected.")
