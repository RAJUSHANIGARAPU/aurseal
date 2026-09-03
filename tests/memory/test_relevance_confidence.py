"""Relevance has to reach the confidence number, and absence is not a match.

Two defects, one symptom: the engine could be certain about records it had
never established were related. ``rank()`` computed a relevance score per
record and discarded it, and ``_jaccard`` treated two empty sets as identical,
so a record with no endpoint at all took the full 0.50 endpoint weight.

Both halves were observed failing against the unfixed code.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from memory.intelligence import FailureIntelligenceEngine
from memory.models import MemoryRecord
from memory.normalize import ID_PLACEHOLDER
from memory.retriever import MemoryRetriever, _jaccard, _path_segments
from memory.summarizer import MemorySummarizer


def _record(
    endpoint: str = "/api/orders",
    error: str = "connection reset while creating an order",
    *,
    outcome: str = "resolved",
    days_old: int = 0,
) -> MemoryRecord:
    ts = (datetime.now(tz=timezone.utc) - timedelta(days=days_old)).isoformat()
    return MemoryRecord(
        id=uuid.uuid4().hex,
        test_id="tests/test_orders.py::test_create_order",
        endpoint=endpoint,
        method="POST",
        category="ENV_ERROR",
        error_signature=error,
        root_cause="connection reset",
        fix_strategy="raise the order-service connection pool",
        fix_outcome=outcome,
        environment="qa",
        run_id="run",
        timestamp=ts,
        ttl_days=90,
    )


# ------------------------------------------------------------------
# Defect 4 — empty vs empty scored as a perfect match
# ------------------------------------------------------------------


def test_an_absent_endpoint_earns_no_credit_over_a_real_one():
    """The reported numbers, as a test.

    Every from_pytest_report record has endpoint="" (summarizer.py:115). For a
    query that also has no endpoint, empty-vs-empty scored a perfect 1.0 and
    took the whole 0.50 endpoint weight: the pytest record came out at 0.70
    against 0.20 for a record on a real, domain-matching endpoint.
    """
    r = MemoryRetriever()
    error = "connection reset while creating an order"

    endpoint_less = r.score(_record(endpoint=""), error, "")
    real_endpoint = r.score(_record(endpoint="/api/orders"), error, "")

    assert endpoint_less <= real_endpoint
    assert endpoint_less < 0.7


def test_endpoint_less_record_does_not_outrank_one_with_a_matching_endpoint():
    r = MemoryRetriever(top_k=2)
    error = "connection reset while creating an order"
    pytest_style = _record(endpoint="")
    real = _record(endpoint="/api/orders")

    assert r.score(real, error, "/api/orders") > r.score(pytest_style, error, "/api/orders")
    assert r.rank([pytest_style, real], error, "/api/orders")[0].endpoint == "/api/orders"


def test_two_endpoint_less_values_score_no_endpoint_similarity():
    r = MemoryRetriever()
    only_error_and_recency = r.score(_record(endpoint=""), "nothing alike here", "")
    # 0.50 endpoint weight must be forfeited entirely, not awarded.
    assert only_error_and_recency <= 0.5


def test_endpoint_less_record_is_still_retrievable_when_it_is_the_best_there_is():
    """POSITIVE CONTROL — scoring 0 on endpoint must not make a record invisible."""
    r = MemoryRetriever(top_k=3)
    only = _record(endpoint="")

    assert r.rank([only], "connection reset while creating an order", "/api/orders") == [only]


def test_id_placeholder_is_not_counted_as_a_shared_path_segment():
    """{id} says nothing about which resource this is — both sides always have it.

    Counting it would put /api/users/{id} and /api/orders/{id} at 2/3 similar
    on the strength of "both take an id", instead of the 1/3 they earn from
    sharing /api.
    """
    users = MemorySummarizer.normalize_endpoint("/api/users/42")
    orders = MemorySummarizer.normalize_endpoint("/api/orders/42")

    assert ID_PLACEHOLDER not in _path_segments(users)
    assert _jaccard(_path_segments(users), _path_segments(orders)) == 1 / 3


def test_matching_paths_still_score_a_perfect_endpoint_similarity():
    """POSITIVE CONTROL for the placeholder filter."""
    a = MemorySummarizer.normalize_endpoint("/api/users/42/orders")
    b = MemorySummarizer.normalize_endpoint("/api/users/99/orders")

    assert _jaccard(_path_segments(a), _path_segments(b)) == 1.0


# ------------------------------------------------------------------
# Defect 6 — confidence ignored similarity entirely
# ------------------------------------------------------------------


def test_rank_scored_hands_back_the_scores_it_computed():
    r = MemoryRetriever(top_k=2)
    records = [_record(endpoint="/api/unrelated/thing"), _record(endpoint="/api/orders")]

    scored = r.rank_scored(records, "connection reset while creating an order", "/api/orders")

    assert [rec.endpoint for _, rec in scored] == ["/api/orders", "/api/unrelated/thing"]
    assert scored[0][0] > scored[1][0]
    assert all(0.0 <= score <= 1.0 for score, _ in scored)


def test_confidence_collapses_when_the_records_are_not_similar():
    """Three all-resolved records used to give 1.0 regardless of relevance."""
    records = [_record() for _ in range(3)]

    assert FailureIntelligenceEngine._compute_confidence(records, [0.05, 0.04, 0.03]) < 0.1


def test_confidence_stays_high_when_the_records_really_do_match():
    """POSITIVE CONTROL — the similarity factor must not deflate genuine evidence."""
    records = [_record() for _ in range(3)]

    assert FailureIntelligenceEngine._compute_confidence(records, [1.0, 0.98, 0.95]) >= 0.9


def test_confidence_is_zero_when_relevance_was_never_measured():
    """No scores means nobody checked. That is 0.0, not the volume-only number."""
    records = [_record() for _ in range(3)]

    assert FailureIntelligenceEngine._compute_confidence(records, []) == 0.0
