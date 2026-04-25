"""Pytest plugin that silently records test outcomes into the memory store.

Registration (automatic via conftest.py when ENABLE_MEMORY=true):
    plugin = MemoryPlugin.from_config(mem_config)
    config.pluginmanager.register(plugin, "memory-tracker")

Works for both UI tests (Playwright) and API tests run via pytest.
Does NOT interfere with the FlakinessPlugin — they write to different stores.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from memory.summarizer import MemorySummarizer

if TYPE_CHECKING:
    from memory.config import MemoryConfig
    from memory.store import MemoryStore


class MemoryPlugin:
    """Records per-test outcomes into the persistent memory store."""

    def __init__(
        self, store: MemoryStore, summarizer: MemorySummarizer, config: MemoryConfig
    ) -> None:
        self._store = store
        self._summarizer = summarizer
        self._config = config
        self._run_id = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self._environment = "qa"

    @classmethod
    def from_config(cls, config: MemoryConfig) -> MemoryPlugin:
        from memory.store import MemoryStore

        store = MemoryStore(config)
        summarizer = MemorySummarizer(ttl_days=config.ttl_days)
        return cls(store=store, summarizer=summarizer, config=config)

    # ------------------------------------------------------------------
    # pytest hooks
    # ------------------------------------------------------------------

    def pytest_configure(self, config) -> None:  # noqa: ARG002
        """Capture --env option after it is registered."""
        try:
            env = config.getoption("--env", default="qa")
            self._environment = env or "qa"
        except (ValueError, AttributeError):
            pass

    def pytest_runtest_logreport(self, report) -> None:
        """Store failed tests; in active mode also store passes (for resolution tracking)."""
        if report.when != "call":
            return

        is_failure = report.failed
        is_success = report.passed

        # Always capture failures; capture successes only in active mode
        # (so we can mark prior failures as resolved)
        if not is_failure and not (is_success and self._config.mode == "active"):
            return

        outcome = "failed" if is_failure else "passed"
        error = str(report.longrepr)[:2000] if is_failure else ""
        worker = os.environ.get("PYTEST_XDIST_WORKER", "main")

        record = self._summarizer.from_pytest_report(
            node_id=report.nodeid,
            outcome=outcome,
            error=error,
            run_id=self._run_id,
            environment=self._environment,
            duration_s=getattr(report, "duration", 0.0),
        )
        record.metadata["worker"] = worker
        self._store.save(record)

        if is_success and self._config.mode == "active":
            self._store.update_outcome(report.nodeid, "resolved")

    def pytest_sessionfinish(self, session, exitstatus) -> None:  # noqa: ARG002
        """Prune expired records at the end of every session."""
        pruned = self._store.prune_expired()
        if pruned:
            import logging

            logging.getLogger(__name__).debug("[memory] pruned %d expired record(s)", pruned)
