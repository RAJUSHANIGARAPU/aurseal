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

from memory.normalize import ID_PLACEHOLDER

if TYPE_CHECKING:
    from memory.models import MemoryRecord

_TOKEN_RE = re.compile(r"[a-zA-Z0-9]+")
_NOISE_WORDS = frozenset({"the", "a", "an", "in", "on", "at", "to", "for", "of", "and", "or"})


def _tokens(text: str) -> frozenset[str]:
    return frozenset(w.lower() for w in _TOKEN_RE.findall(text) if w.lower() not in _NOISE_WORDS)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    """Overlap of two token sets. Two empty sets share nothing, they match nothing.

    This returned 1.0 for empty-vs-empty, and that one line let records with no
    endpoint outrank records with a real one. Every ``from_pytest_report``
    record carries ``endpoint=""``, so for a query with no endpoint a pytest
    record scored 0.70 while a genuine record on a related endpoint scored
    0.20 — the absence of data took the entire 0.50 endpoint weight as a
    perfect match. Nothing compared to nothing is no evidence, so it is 0.0.
    """
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _path_segments(url: str) -> frozenset[str]:
    url = url.split("?")[0].lower()
    return frozenset(
        s
        for s in url.split("/")
        # {id} is the placeholder both the write and the read path substitute
        # for an id-shaped segment. It says nothing about which resource this
        # is, so counting it would make /api/users/{id} and /api/orders/{id}
        # look one-third similar purely because both take an id.
        if s and s != ID_PLACEHOLDER and not s.isdigit() and len(s) > 2
    )


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
        return [r for _, r in self.rank_scored(records, query_error, query_endpoint)]

    def rank_scored(
        self,
        records: list[MemoryRecord],
        query_error: str,
        query_endpoint: str,
    ) -> list[tuple[float, MemoryRecord]]:
        """Top-k as ``(score, record)`` pairs, highest first.

        ``rank`` computed these scores and threw them away, so every consumer
        downstream treated "the retriever returned three rows" as if it meant
        "three relevant rows" — which is how three records scoring near zero
        produced a confidence of 1.0. Callers that judge relevance need the
        number, not just the ordering.
        """
        if not records:
            return []

        scored = [(self._score(r, query_error, query_endpoint), r) for r in records]
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[: self._top_k]

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
