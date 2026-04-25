"""Ranks candidate MemoryRecords by relevance to a current failure.

Scoring formula (all components in [0, 1]):
    score = 0.50 * endpoint_similarity
          + 0.30 * error_keyword_overlap
          + 0.20 * recency_score

- endpoint_similarity: Jaccard on URL path segments
- error_keyword_overlap: Jaccard on word tokens from error messages
- recency_score: exponential decay with half-life = 30 days

No ML, no embeddings — deterministic and fast enough for CI.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from memory.models import MemoryRecord

_TOKEN_RE = re.compile(r"[a-zA-Z0-9]+")
_NOISE_WORDS = frozenset({"the", "a", "an", "in", "on", "at", "to", "for", "of", "and", "or"})


def _tokens(text: str) -> frozenset[str]:
    return frozenset(w.lower() for w in _TOKEN_RE.findall(text) if w.lower() not in _NOISE_WORDS)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _path_segments(url: str) -> frozenset[str]:
    url = url.split("?")[0].lower()
    return frozenset(s for s in url.split("/") if s and not s.isdigit() and len(s) > 2)


def _recency_score(timestamp: str, half_life_days: float = 30.0) -> float:
    """Exponential decay: 1.0 at creation, ~0.5 after half_life_days."""
    try:
        ts = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        days_old = (datetime.now(tz=timezone.utc) - ts).total_seconds() / 86400
        return math.exp(-math.log(2) * days_old / half_life_days)
    except Exception:  # noqa: BLE001
        return 0.0


class MemoryRetriever:
    """Scores and ranks memory records for a given query context."""

    def __init__(self, top_k: int = 3) -> None:
        self._top_k = top_k

    def rank(
        self,
        records: list[MemoryRecord],
        query_error: str,
        query_endpoint: str,
    ) -> list[MemoryRecord]:
        """Return top-k records sorted by descending relevance score."""
        if not records:
            return []

        scored = [(self._score(r, query_error, query_endpoint), r) for r in records]
        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in scored[: self._top_k]]

    def score(self, record: MemoryRecord, query_error: str, query_endpoint: str) -> float:
        """Public accessor for individual score — useful for testing."""
        return self._score(record, query_error, query_endpoint)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _score(self, record: MemoryRecord, query_error: str, query_endpoint: str) -> float:
        endpoint_sim = _jaccard(
            _path_segments(query_endpoint),
            _path_segments(record.endpoint),
        )
        error_overlap = _jaccard(
            _tokens(query_error),
            _tokens(record.error_signature),
        )
        recency = _recency_score(record.timestamp)
        return 0.50 * endpoint_sim + 0.30 * error_overlap + 0.20 * recency
