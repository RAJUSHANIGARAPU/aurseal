"""Tests for memory.retriever.MemoryRetriever."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from memory.models import MemoryRecord
from memory.retriever import MemoryRetriever, _jaccard, _path_segments, _tokens


def _record(
    endpoint: str = "/api/users",
    error: str = "AssertionError expected 200",
    days_old: int = 0,
    fix_outcome: str = "pending",
) -> MemoryRecord:
    ts = (datetime.now(tz=timezone.utc) - timedelta(days=days_old)).isoformat()
    return MemoryRecord(
        id=uuid.uuid4().hex,
        test_id="test_x",
        endpoint=endpoint,
        method="GET",
        category="ASSERTION_ERROR",
        error_signature=error,
        root_cause="root",
        fix_strategy="fix",
        fix_outcome=fix_outcome,
        environment="qa",
        run_id="run",
        timestamp=ts,
        ttl_days=90,
    )


# ------------------------------------------------------------------
# Jaccard & tokenisation helpers
# ------------------------------------------------------------------


def test_jaccard_identical_sets():
    a = frozenset({"a", "b", "c"})
    assert _jaccard(a, a) == 1.0


def test_jaccard_disjoint_sets():
    assert _jaccard(frozenset({"a"}), frozenset({"b"})) == 0.0


def test_jaccard_partial_overlap():
    a = frozenset({"a", "b"})
    b = frozenset({"b", "c"})
    assert _jaccard(a, b) == pytest.approx(1 / 3, abs=1e-6)


def test_jaccard_both_empty():
    assert _jaccard(frozenset(), frozenset()) == 1.0


def test_tokens_strips_noise_words():
    tokens = _tokens("the request to the server failed")
    assert "the" not in tokens
    assert "to" not in tokens
    assert "failed" in tokens


def test_path_segments_strips_short_segments():
    # single-char and two-char segments filtered out
    segs = _path_segments("/api/v1/users")
    assert "v1" not in segs  # len <= 2
    assert "api" in segs
    assert "users" in segs


# ------------------------------------------------------------------
# rank()
# ------------------------------------------------------------------


def test_rank_empty_records_returns_empty():
    r = MemoryRetriever(top_k=3)
    assert r.rank([], "error", "/api/users") == []


def test_rank_respects_top_k():
    r = MemoryRetriever(top_k=2)
    records = [_record() for _ in range(5)]
    result = r.rank(records, "error", "/api/users")
    assert len(result) <= 2


def test_rank_exact_endpoint_scores_higher():
    r = MemoryRetriever(top_k=3)
    exact = _record(endpoint="/api/users")
    other = _record(endpoint="/api/completely/different")
    ranked = r.rank([other, exact], "same error", "/api/users")
    assert ranked[0].endpoint == "/api/users"


def test_rank_similar_error_scores_higher():
    r = MemoryRetriever(top_k=3)
    matching = _record(endpoint="/api/x", error="AssertionError expected 200 got 404")
    mismatched = _record(endpoint="/api/x", error="TimeoutError connection refused completely")
    ranked = r.rank([mismatched, matching], "AssertionError expected 200", "/api/x")
    assert ranked[0].error_signature == matching.error_signature


def test_rank_recent_beats_old_when_otherwise_equal():
    r = MemoryRetriever(top_k=3)
    old = _record(endpoint="/api/x", error="same error", days_old=60)
    recent = _record(endpoint="/api/x", error="same error", days_old=0)
    ranked = r.rank([old, recent], "same error", "/api/x")
    # Recent should be ranked first
    assert ranked[0].timestamp > ranked[1].timestamp


# ------------------------------------------------------------------
# score()
# ------------------------------------------------------------------


def test_score_returns_float_in_range():
    r = MemoryRetriever()
    rec = _record()
    s = r.score(rec, "some error", "/api/users")
    assert 0.0 <= s <= 1.0


def test_score_perfect_match_near_one():
    r = MemoryRetriever()
    rec = _record(endpoint="/api/users", error="AssertionError failed check", days_old=0)
    s = r.score(rec, "AssertionError failed check", "/api/users")
    assert s > 0.7
