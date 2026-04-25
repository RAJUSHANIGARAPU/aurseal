"""Failure Intelligence Engine.

Combines memory retrieval with an optional LLM call to produce
MemoryInsight objects that describe what probably went wrong and
what fix is most likely to work — based on historical evidence.

LLM prompt is kept under ~300 tokens input. Response is capped at 256 tokens.
When no similar records exist, the engine returns a low-confidence insight
without calling the LLM (not enough context to enrich).
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from memory.models import MemoryInsight, MemoryRecord

if TYPE_CHECKING:
    from api.agents.analysis import FailureAnalysis
    from api.agents.ingestion import PostmanRequest
    from api.llm.base import BaseLLMClient
    from memory.config import MemoryConfig
    from memory.retriever import MemoryRetriever
    from memory.store import MemoryStore

logger = logging.getLogger(__name__)

_MIN_RECORDS_FOR_LLM = 1  # only call LLM when we have at least one historical record


class FailureIntelligenceEngine:
    """Produces MemoryInsight for a current failure using historical memory."""

    def __init__(
        self,
        store: MemoryStore,
        retriever: MemoryRetriever,
        config: MemoryConfig,
        llm: BaseLLMClient | None = None,
    ) -> None:
        self._store = store
        self._retriever = retriever
        self._config = config
        self._llm = llm

    def analyze(
        self,
        analysis: FailureAnalysis,
        request: PostmanRequest | None = None,
    ) -> MemoryInsight:
        """Return a MemoryInsight for the given failure.

        1. Query store for records near this endpoint + category
        2. Rank by similarity to current error
        3. If records found and LLM enabled: enrich with LLM
        4. Return MemoryInsight
        """
        endpoint = request.url if request else ""
        candidates = self._store.query(endpoint=endpoint, category=analysis.category.value)

        ranked = self._retriever.rank(
            candidates,
            query_error=analysis.raw_message,
            query_endpoint=endpoint,
        )

        confidence = self._compute_confidence(ranked)
        pattern_summary = self._summarize_records(ranked)
        suggested_fix = self._best_fix(ranked, analysis.suggested_fix)

        llm_analysis = ""
        use_llm = self._config.llm_enabled and self._llm and len(ranked) >= _MIN_RECORDS_FOR_LLM
        if use_llm:
            llm_analysis = self._llm_enrich(analysis, ranked)

        return MemoryInsight(
            test_id=analysis.test_name,
            similar_records=ranked,
            pattern_summary=pattern_summary,
            suggested_fix=suggested_fix,
            confidence=confidence,
            llm_analysis=llm_analysis,
        )

    def _llm_enrich(self, analysis: FailureAnalysis, similar: list[MemoryRecord]) -> str:
        prompt = self._build_prompt(analysis, similar)
        raw = self._llm.complete(prompt, max_tokens=256) if self._llm else ""  # type: ignore[union-attr]
        if not raw or raw.startswith("Claude API error"):
            return ""
        return raw.strip()

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

    def _build_prompt(self, analysis: FailureAnalysis, similar: list[MemoryRecord]) -> str:
        history_lines = []
        for i, rec in enumerate(similar, 1):
            history_lines.append(
                f"{i}. [{rec.timestamp[:10]}] {rec.test_id}\n"
                f"   Error: {rec.error_signature[:150]}\n"
                f"   Fix: {rec.fix_strategy[:100]} → {rec.fix_outcome}"
            )
        history_block = "\n".join(history_lines) if history_lines else "No historical data."

        return (
            "You are a QA engineer with access to historical test failure data.\n\n"
            f"CURRENT FAILURE:\n"
            f"- Test: {analysis.test_name}\n"
            f"- Category: {analysis.category.value}\n"
            f"- Error: {analysis.raw_message[:200]}\n"
            f"- Initial diagnosis: {analysis.root_cause}\n\n"
            f"SIMILAR PAST FAILURES ({len(similar)} found):\n{history_block}\n\n"
            "Based on the above, respond with a JSON object only (no markdown):\n"
            '{"probable_root_cause": "...", "suggested_fix": "...", '
            '"confidence": 0.0, "pattern_observed": "..."}\n'
            "Keep each value under 100 words."
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_confidence(records: list[MemoryRecord]) -> float:
        """Higher confidence when past fixes were marked as resolved."""
        if not records:
            return 0.0
        resolved = sum(1 for r in records if r.fix_outcome == "resolved")
        base = min(len(records) / 5, 0.6)  # up to 0.6 from volume
        resolution_bonus = (resolved / len(records)) * 0.4  # up to 0.4 from success rate
        return round(base + resolution_bonus, 2)

    @staticmethod
    def _summarize_records(records: list[MemoryRecord]) -> str:
        if not records:
            return "No similar historical failures found."
        categories = {r.category for r in records}
        outcomes = [r.fix_outcome for r in records]
        resolved = outcomes.count("resolved")
        return (
            f"{len(records)} similar failure(s) found "
            f"(categories: {', '.join(sorted(categories))}). "
            f"{resolved}/{len(records)} previously resolved."
        )

    @staticmethod
    def _best_fix(records: list[MemoryRecord], fallback: str) -> str:
        resolved = [r for r in records if r.fix_outcome == "resolved" and r.fix_strategy]
        if resolved:
            return resolved[0].fix_strategy
        with_strategy = [r for r in records if r.fix_strategy]
        if with_strategy:
            return with_strategy[0].fix_strategy
        return fallback

    @staticmethod
    def _parse_llm_json(text: str) -> dict:
        try:
            text = text.strip()
            if text.startswith("{"):
                return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            pass
        return {}
