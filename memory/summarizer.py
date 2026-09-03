"""Converts raw test execution data into compact MemoryRecord objects.

Rules for what gets stored:
- Endpoint: normalized (UUIDs, numeric IDs, query params stripped)
- Error: timestamps/addresses stripped, truncated at 500 chars
- Payload: first 200 chars only (prevents large body storage)
- Root cause: first line of error message, truncated at 200 chars
- No raw request/response bodies — only signatures
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from memory.models import MemoryRecord
from memory.normalize import normalize_endpoint as _canonical_endpoint

if TYPE_CHECKING:
    from api.agents.analysis import FailureAnalysis
    from api.agents.ingestion import PostmanRequest

_TIMESTAMP_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?"
)
_ADDR_RE = re.compile(r"0x[0-9a-fA-F]+")
_TRACEBACK_LINES_RE = re.compile(r'^\s+File ".+", line \d+', re.MULTILINE)

_MAX_ERROR_LEN = 500
_MAX_PAYLOAD_LEN = 200
_MAX_ROOT_CAUSE_LEN = 200


class MemorySummarizer:
    """Produces lean MemoryRecord objects from richer agent data."""

    def __init__(self, ttl_days: int = 90, environment: str = "qa") -> None:
        self._ttl_days = ttl_days
        self._environment = environment

    def from_failure_analysis(
        self,
        analysis: FailureAnalysis,
        request: PostmanRequest | None,
        run_id: str,
        environment: str | None = None,
        fix_outcome: str = "pending",
    ) -> MemoryRecord:
        """Build a MemoryRecord from a FailureAnalysis (+ optional PostmanRequest)."""
        endpoint = self._normalize_endpoint(request.url if request else "")
        method = (request.method if request else "").upper()
        payload_snippet = self._payload_snippet(request)

        return MemoryRecord(
            id=uuid.uuid4().hex,
            test_id=analysis.test_name,
            endpoint=endpoint,
            method=method,
            category=analysis.category.value,
            error_signature=self._normalize_error(analysis.raw_message),
            root_cause=analysis.root_cause[:_MAX_ROOT_CAUSE_LEN],
            fix_strategy=analysis.suggested_fix[:_MAX_ROOT_CAUSE_LEN],
            fix_outcome=fix_outcome,
            environment=environment or self._environment,
            run_id=run_id,
            timestamp=datetime.now(tz=timezone.utc).isoformat(),
            ttl_days=self._ttl_days,
            payload_snippet=payload_snippet,
            status_code=None,
            metadata={"llm_diagnosis": analysis.llm_diagnosis},
        )

    def from_success(
        self,
        test_name: str,
        request: PostmanRequest | None,
        run_id: str,
        environment: str | None = None,
    ) -> MemoryRecord:
        """Build a success MemoryRecord (lighter — no error fields)."""
        return MemoryRecord(
            id=uuid.uuid4().hex,
            test_id=test_name,
            endpoint=self._normalize_endpoint(request.url if request else ""),
            method=(request.method if request else "").upper(),
            category="success",
            error_signature="",
            root_cause="",
            fix_strategy="",
            fix_outcome="resolved",
            environment=environment or self._environment,
            run_id=run_id,
            timestamp=datetime.now(tz=timezone.utc).isoformat(),
            ttl_days=self._ttl_days,
            payload_snippet=self._payload_snippet(request),
            status_code=None,
        )

    def from_pytest_report(
        self,
        node_id: str,
        outcome: str,
        error: str,
        run_id: str,
        environment: str | None = None,
        duration_s: float = 0.0,
    ) -> MemoryRecord:
        """Build a MemoryRecord directly from a pytest report (UI/integration tests)."""
        return MemoryRecord(
            id=uuid.uuid4().hex,
            test_id=node_id,
            endpoint="",
            method="",
            category="failure" if outcome == "failed" else outcome,
            error_signature=self._normalize_error(error),
            root_cause=self._first_line(error),
            fix_strategy="",
            fix_outcome="pending" if outcome == "failed" else "resolved",
            environment=environment or self._environment,
            run_id=run_id,
            timestamp=datetime.now(tz=timezone.utc).isoformat(),
            ttl_days=self._ttl_days,
            metadata={"duration_s": duration_s},
        )

    # ------------------------------------------------------------------
    # Normalization helpers (static — used by retriever too)
    # ------------------------------------------------------------------

    @staticmethod
    def normalize_endpoint(url: str) -> str:
        """Public alias for tests."""
        return MemorySummarizer._normalize_endpoint(url)

    @staticmethod
    def normalize_error(error: str) -> str:
        """Public alias for tests."""
        return MemorySummarizer._normalize_error(error)

    @staticmethod
    def _normalize_endpoint(url: str) -> str:
        # Delegates to memory.normalize so the read path in MemoryStore.query
        # computes the identical string. The two used to differ ({id} here,
        # "" there) and id-bearing endpoints scored 0 hits as a result.
        return _canonical_endpoint(url)

    @staticmethod
    def _normalize_error(error: str) -> str:
        if not error:
            return ""
        cleaned = _TIMESTAMP_RE.sub("", error)
        cleaned = _ADDR_RE.sub("", cleaned)
        cleaned = _TRACEBACK_LINES_RE.sub("", cleaned)
        cleaned = " ".join(cleaned.split())
        return cleaned[:_MAX_ERROR_LEN]

    @staticmethod
    def _first_line(text: str) -> str:
        for line in text.splitlines():
            stripped = line.strip()
            if stripped:
                return stripped[:_MAX_ROOT_CAUSE_LEN]
        return text[:_MAX_ROOT_CAUSE_LEN]

    @staticmethod
    def _payload_snippet(request: PostmanRequest | None) -> str:
        if request is None or request.body is None:
            return ""
        body = str(request.body)
        return body[:_MAX_PAYLOAD_LEN]
