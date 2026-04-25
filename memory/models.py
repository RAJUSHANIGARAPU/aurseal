"""Core data models for the Memory Intelligence Layer."""

from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class MemoryRecord:
    """A single stored test execution event — failure or success.

    Schema is deliberately lean: only high-signal fields are persisted.
    Raw request/response bodies are never stored — only normalized signatures.
    """

    id: str  # UUID hex
    test_id: str  # test name or pytest node id
    endpoint: str  # normalized URL pattern (UUIDs/IDs stripped)
    method: str  # GET | POST | PUT | PATCH | DELETE
    category: str  # FailureCategory.value or "success"
    error_signature: str  # cleaned, truncated error message (≤500 chars)
    root_cause: str  # extracted root cause (≤200 chars)
    fix_strategy: str  # what fix was applied or suggested
    fix_outcome: str  # "pending" | "resolved" | "unresolved"
    environment: str  # "qa" | "staging" | "prod"
    run_id: str  # orchestration session id
    timestamp: str  # ISO-8601 UTC
    ttl_days: int  # days until this record is pruned
    payload_snippet: str = ""  # first 200 chars of request body
    status_code: int | None = None  # HTTP status code
    metadata: dict = field(default_factory=dict)  # arbitrary extras

    # ------------------------------------------------------------------
    # SQLite row serialisation
    # ------------------------------------------------------------------

    def to_row(self) -> tuple:
        return (
            self.id,
            self.test_id,
            self.endpoint,
            self.method,
            self.category,
            self.error_signature,
            self.root_cause,
            self.fix_strategy,
            self.fix_outcome,
            self.environment,
            self.run_id,
            self.timestamp,
            self.ttl_days,
            self.payload_snippet,
            self.status_code,
            json.dumps(self.metadata),
        )

    @classmethod
    def from_row(cls, row: tuple) -> MemoryRecord:
        (
            id_,
            test_id,
            endpoint,
            method,
            category,
            error_sig,
            root_cause,
            fix_strategy,
            fix_outcome,
            environment,
            run_id,
            timestamp,
            ttl_days,
            payload_snippet,
            status_code,
            metadata_json,
        ) = row
        return cls(
            id=id_,
            test_id=test_id,
            endpoint=endpoint,
            method=method,
            category=category,
            error_signature=error_sig,
            root_cause=root_cause,
            fix_strategy=fix_strategy,
            fix_outcome=fix_outcome,
            environment=environment,
            run_id=run_id,
            timestamp=timestamp,
            ttl_days=ttl_days,
            payload_snippet=payload_snippet or "",
            status_code=status_code,
            metadata=json.loads(metadata_json) if metadata_json else {},
        )


@dataclass
class MemoryInsight:
    """Enriched insight surfaced to the LLM and downstream agents.

    Produced by FailureIntelligenceEngine for each failure, combining
    retrieved historical records with optional LLM-generated analysis.
    """

    test_id: str
    similar_records: list[MemoryRecord]
    pattern_summary: str  # human-readable summary of what was found
    suggested_fix: str  # best fix derived from history
    confidence: float  # 0.0–1.0 — higher when past fixes resolved the issue
    llm_analysis: str = ""  # enriched detail from LLM (empty when LLM disabled)
