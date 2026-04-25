"""Pytest plugin that silently records every test outcome for flakiness analysis.

Registration (in root conftest.py pytest_configure):
    from autonomous_ui.flakiness.pytest_plugin import FlakinessPlugin
    config.pluginmanager.register(FlakinessPlugin.from_config(config), "flakiness-tracker")

The plugin adds zero latency to tests — the JSONL write is fire-and-forget.
It also generates a flakiness report at the end of every session with enough history.
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


class FlakinessPlugin:
    """Records test outcomes and emits a flakiness report at session end."""

    def __init__(self, store: HistoryStore, run_id: str, environment: str) -> None:
        self._store = store
        self._run_id = run_id
        self._environment = environment

    @classmethod
    def from_config(cls, config) -> FlakinessPlugin:
        run_id = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        try:
            env = config.getoption("--env", default="qa")
        except (ValueError, AttributeError):
            env = "qa"
        return cls(store=HistoryStore(), run_id=run_id, environment=env)

    # ------------------------------------------------------------------
    # pytest hooks
    # ------------------------------------------------------------------

    def pytest_runtest_logreport(self, report) -> None:
        if report.when != "call":
            return

        outcome = "passed" if report.passed else "failed" if report.failed else "skipped"
        error = str(report.longrepr)[:2000] if report.failed else ""
        worker = os.environ.get("PYTEST_XDIST_WORKER", "main")

        self._store.record(
            FlakRecord(
                test_id=report.nodeid,
                run_id=self._run_id,
                outcome=outcome,
                duration_s=getattr(report, "duration", 0.0),
                error=error,
                timestamp=datetime.now(tz=timezone.utc).isoformat(),
                worker=worker,
                environment=self._environment,
            )
        )

    def pytest_sessionfinish(self, session, exitstatus) -> None:  # noqa: ARG002
        """Generate a flakiness report if there is enough history."""
        detector = FlakinessDetector(self._store)
        profiles = detector.get_profiles()
        if not profiles:
            return

        analyzer = PatternAnalyzer()
        remediator = FlakinessRemediator()
        groups = self._store.grouped_by_test()
        analyses = {}
        for profile in profiles:
            if not profile.is_flaky:
                continue
            records = groups.get(profile.test_id, [])
            pattern = analyzer.classify(profile, records, use_llm=False)
            remediation = remediator.remediate(profile, pattern, records)
            analyses[profile.test_id] = (pattern, remediation)

        flaky_count = sum(1 for p in profiles if p.is_flaky)
        if flaky_count > 0:
            reporter = FlakinessReporter()
            md_path, json_path = reporter.write(profiles, analyses)
            print(f"\n[flakiness] {flaky_count} flaky test(s) detected." f" Report: {md_path}")
        else:
            print(f"\n[flakiness] {len(profiles)} test(s) tracked — no flakiness detected.")
