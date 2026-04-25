"""MemoryMiddleware: integrates the memory layer into the Orchestrator.

Called at two points in the orchestration loop:
  1. before_execution()  — retrieve relevant memories, inject into ContextMemory
  2. after_execution()   — store new failure records, return enriched MemoryInsights

Both calls are no-ops when mode != "active" (before_execution) or when disabled.
No existing orchestrator behaviour is modified — this is purely additive.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from memory.intelligence import FailureIntelligenceEngine
from memory.summarizer import MemorySummarizer

if TYPE_CHECKING:
    from api.agents.analysis import FailureAnalysis
    from api.agents.ingestion import PostmanRequest
    from api.engine.context_memory import ContextMemory
    from api.llm.base import BaseLLMClient
    from memory.config import MemoryConfig
    from memory.models import MemoryInsight
    from memory.retriever import MemoryRetriever
    from memory.store import MemoryStore

logger = logging.getLogger(__name__)


class MemoryMiddleware:
    """Thin wrapper that plugs memory read/write into the orchestrator at safe points."""

    def __init__(
        self,
        store: MemoryStore,
        retriever: MemoryRetriever,
        config: MemoryConfig,
        llm: BaseLLMClient | None = None,
    ) -> None:
        self._store = store
        self._config = config
        self._summarizer = MemorySummarizer(ttl_days=config.ttl_days)
        self._intelligence = FailureIntelligenceEngine(store, retriever, config, llm)

    # ------------------------------------------------------------------
    # Hook: before first execution
    # ------------------------------------------------------------------

    def before_execution(
        self,
        requests: list[PostmanRequest],
        ctx: ContextMemory,
    ) -> None:
        """Retrieve relevant memories and inject into ContextMemory.

        Only runs in active mode — passive mode is write-only.
        """
        if self._config.mode != "active":
            return

        summaries: list[str] = []
        for req in requests:
            records = self._store.get_for_test(req.name)
            if not records:
                records = self._store.query(endpoint=req.url)
            if records:
                failures = [r for r in records if r.category != "success"]
                if failures:
                    top = failures[0]
                    summaries.append(
                        f"[{req.name}] Past failure — {top.category}: {top.error_signature[:80]} "
                        f"(fix: {top.fix_strategy[:60]}, outcome: {top.fix_outcome})"
                    )

        if summaries:
            ctx.set("memory_insights", summaries)
            logger.debug("[memory] injected %d insight(s) into context", len(summaries))

    # ------------------------------------------------------------------
    # Hook: after each analysis cycle
    # ------------------------------------------------------------------

    def after_execution(
        self,
        analyses: list[FailureAnalysis],
        requests: list[PostmanRequest],
        run_id: str,
        environment: str = "qa",
    ) -> list[MemoryInsight]:
        """Store failures and return enriched MemoryInsight objects.

        Runs in both passive and active mode.
        """
        request_map = {req.name: req for req in requests}
        insights: list[MemoryInsight] = []

        for fa in analyses:
            req = request_map.get(fa.test_name)
            record = self._summarizer.from_failure_analysis(fa, req, run_id, environment)
            self._store.save(record)
            logger.debug("[memory] stored failure record for %s", fa.test_name)

            insight = self._intelligence.analyze(fa, req)
            insights.append(insight)

        return insights

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def mark_resolved(self, test_name: str) -> None:
        """Call after a previously-failing test passes to update its fix_outcome."""
        self._store.update_outcome(test_name, "resolved")
        logger.debug("[memory] marked %s as resolved", test_name)

    def prune(self) -> int:
        return self._store.prune_expired()
