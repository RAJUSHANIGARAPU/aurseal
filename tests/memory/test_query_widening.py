"""The memory layer must not answer a question it was not asked.

Every test here was written against the unfixed code first and observed to
fail. They cover one failure mode with several faces: a filter that cannot be
applied was dropped instead of respected, so the store answered "here is
everything" to a request for "records near this endpoint", and the layers above
read that as evidence.

Each "must not return unrelated records" test is paired with a positive control
on the same store, because a store that returned nothing at all would satisfy
the negative half of every one of them.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from memory.config import MemoryConfig
from memory.intelligence import FailureIntelligenceEngine
from memory.models import MemoryRecord
from memory.normalize import escape_like, is_discriminating, normalize_endpoint
from memory.retriever import MemoryRetriever
from memory.store import MemoryStore, _endpoint_stem
from memory.summarizer import MemorySummarizer


class FailureCategory(str, Enum):
    ENV_ERROR = "ENV_ERROR"


@dataclass
class FakeAnalysis:
    test_name: str = "tests/test_orders.py::test_create_order"
    category: FailureCategory = FailureCategory.ENV_ERROR
    root_cause: str = "connection reset"
    suggested_fix: str = "no historical fix available"
    raw_message: str = "connection reset while creating an order"
    llm_diagnosis: dict = field(default_factory=dict)


@dataclass
class FakeRequest:
    name: str = "Create Order"
    method: str = "POST"
    url: str = "https://api.example.com/api/orders"
    body: dict | None = None


def _config(tmp_path: Path, **kw) -> MemoryConfig:
    opts = {
        "enabled": True,
        "db_path": tmp_path / "memory.db",
        "ttl_days": 90,
        "max_records_per_test": 20,
        "similarity_top_k": 3,
        "llm_enabled": False,
    }
    opts.update(kw)
    return MemoryConfig(**opts)


def _record(
    endpoint: str,
    *,
    test_id: str = "tests/test_x.py::test_x",
    outcome: str = "pending",
    category: str = "ENV_ERROR",
    error: str = "connection reset while handling the request",
    fix: str = "",
) -> MemoryRecord:
    return MemoryRecord(
        id=uuid.uuid4().hex,
        test_id=test_id,
        endpoint=endpoint,
        method="GET",
        category=category,
        error_signature=error,
        root_cause="connection reset",
        fix_strategy=fix,
        fix_outcome=outcome,
        environment="qa",
        run_id="run",
        timestamp=datetime.now(tz=timezone.utc).isoformat(),
        ttl_days=90,
    )


def _engine(store: MemoryStore, cfg: MemoryConfig) -> FailureIntelligenceEngine:
    return FailureIntelligenceEngine(store, MemoryRetriever(top_k=3), cfg, None)


def _seed_three_unrelated_resolved(store: MemoryStore) -> None:
    for endpoint, fix in (
        ("/api/billing", "restart the billing worker"),
        ("/api/auth", "rotate the auth token"),
        ("/api/search", "rebuild the search index"),
    ):
        store.save(
            _record(
                endpoint,
                test_id=f"tests/test{endpoint}.py::test_x",
                outcome="resolved",
                fix=fix,
            )
        )


# ------------------------------------------------------------------
# Defect 1 — an absent endpoint disabled the filter instead of narrowing it
# ------------------------------------------------------------------


def test_request_less_failure_does_not_inherit_an_unrelated_endpoints_fix(tmp_path):
    """The reported probe, as a test.

    middleware.after_execution calls analyze(fa, None) for every hand-written
    and UI test, so this is the routine path. Before the fix it returned
    confidence 1.0, "restart the billing worker", and "3 similar failure(s)
    found ... 3/3 previously resolved" for a connection reset on an order.
    """
    cfg = _config(tmp_path)
    store = MemoryStore(cfg)
    _seed_three_unrelated_resolved(store)

    insight = _engine(store, cfg).analyze(FakeAnalysis(), None)

    assert insight.similar_records == []
    assert insight.confidence == 0.0
    assert insight.suggested_fix == "no historical fix available"
    assert "restart the billing worker" not in insight.suggested_fix
    assert "similar failure(s) found" not in insight.pattern_summary
    store.close()


def test_relevant_history_is_still_found_and_still_confident(tmp_path):
    """POSITIVE CONTROL for the test above — the same store, a real endpoint.

    Without this, a store that answered every question with [] would pass the
    whole of this file.
    """
    cfg = _config(tmp_path)
    store = MemoryStore(cfg)
    _seed_three_unrelated_resolved(store)
    for i in range(3):
        store.save(
            _record(
                "https://api.example.com/api/orders",
                test_id=f"tests/test_orders.py::test_{i}",
                outcome="resolved",
                error="connection reset while creating an order",
                fix="raise the order-service connection pool",
            )
        )

    insight = _engine(store, cfg).analyze(FakeAnalysis(), FakeRequest())

    assert len(insight.similar_records) == 3
    assert insight.suggested_fix == "raise the order-service connection pool"
    assert insight.confidence >= 0.8
    assert all("/api/orders" in r.endpoint for r in insight.similar_records)
    store.close()


def test_url_carrying_only_an_id_returns_nothing(tmp_path):
    """/123 is entirely a numeric segment, so nothing about it can narrow.

    It stemmed to '' before the fix and dropped the filter; it stems to '{id}'
    now, which would match every resource-by-id record ever stored. Both are
    widening, so both are rejected.
    """
    store = MemoryStore(_config(tmp_path))
    _seed_three_unrelated_resolved(store)
    store.save(_record("/api/users/7"))

    assert _endpoint_stem("/123") == "{id}"
    assert not is_discriminating(_endpoint_stem("/123"))
    assert store.query(endpoint="/123") == []
    store.close()


def test_an_endpoint_with_one_real_segment_still_narrows(tmp_path):
    """POSITIVE CONTROL — only placeholder-only stems are rejected."""
    store = MemoryStore(_config(tmp_path))
    _seed_three_unrelated_resolved(store)
    store.save(_record("/api/users/7"))

    assert len(store.query(endpoint="/users/99")) == 1
    store.close()


def test_empty_endpoint_returns_nothing_unless_unfiltered_is_requested(tmp_path):
    store = MemoryStore(_config(tmp_path))
    _seed_three_unrelated_resolved(store)

    assert store.query(endpoint="") == []
    # The explicit "no filter" decision the docstring demands.
    assert len(store.query(endpoint="", allow_unfiltered=True)) == 3
    store.close()


# ------------------------------------------------------------------
# Defect 2 — the write path and the read path used different keys
# ------------------------------------------------------------------


def test_id_bearing_endpoint_is_retrievable_after_the_summarizer_stored_it(tmp_path):
    """Stored as .../api/users/{id}/orders, previously queried as .../api/users/orders — 0 hits."""
    store = MemoryStore(_config(tmp_path))
    stored = MemorySummarizer.normalize_endpoint("https://api.example.com/api/users/42/orders")
    assert stored.endswith("/api/users/{id}/orders")
    store.save(_record(stored))

    hits = store.query(endpoint="https://api.example.com/api/users/99/orders")

    assert len(hits) == 1
    store.close()


def test_uuid_bearing_endpoint_is_retrievable(tmp_path):
    store = MemoryStore(_config(tmp_path))
    store.save(
        _record(
            MemorySummarizer.normalize_endpoint(
                "/api/contracts/550e8400-e29b-41d4-a716-446655440000/documents"
            )
        )
    )

    hits = store.query(endpoint="/api/contracts/6ba7b810-9dad-11d1-80b4-00c04fd430c8/documents")

    assert len(hits) == 1
    store.close()


def test_a_different_resource_still_misses(tmp_path):
    """NEGATIVE CONTROL — unifying the key must not make everything match."""
    store = MemoryStore(_config(tmp_path))
    store.save(_record(MemorySummarizer.normalize_endpoint("/api/users/42/orders")))

    assert store.query(endpoint="/api/invoices/42/lines") == []
    store.close()


def test_the_store_normalises_on_write_so_the_key_cannot_depend_on_the_caller(tmp_path):
    store = MemoryStore(_config(tmp_path))
    store.save(_record("/API/Users/42/Orders?page=2"))

    assert store.query(endpoint="/api/users/7/orders")[0].endpoint == "/api/users/{id}/orders"
    store.close()


def test_normalisation_is_idempotent():
    once = normalize_endpoint("/api/users/42/orders")
    assert normalize_endpoint(once) == once


# ------------------------------------------------------------------
# Defect 5 — LIKE wildcards were unescaped
# ------------------------------------------------------------------


def test_underscore_in_a_path_matches_literally(tmp_path):
    """Measured before the fix: /api/user_profile matched a stored /api/userxprofile."""
    store = MemoryStore(_config(tmp_path))
    store.save(_record("/api/userxprofile"))

    assert store.query(endpoint="/api/user_profile") == []
    store.close()


def test_the_genuinely_underscored_path_still_matches(tmp_path):
    """POSITIVE CONTROL — escaping must not stop a real underscore matching itself."""
    store = MemoryStore(_config(tmp_path))
    store.save(_record("/api/user_profile"))

    assert len(store.query(endpoint="/api/user_profile")) == 1
    store.close()


def test_percent_in_a_path_matches_literally(tmp_path):
    store = MemoryStore(_config(tmp_path))
    store.save(_record("/api/reports/anything/summary"))

    assert store.query(endpoint="/api/reports/%/summary") == []
    store.close()


def test_test_id_suffix_lookup_escapes_underscores(tmp_path):
    """get_for_test_suffix has the same hole, and test filenames are all underscores."""
    store = MemoryStore(_config(tmp_path))
    store.save(_record("/api/users", test_id="generated/test-users.py::test-get"))

    assert store.get_for_test_suffix("test_users.py::test_get") == []
    store.close()


def test_test_id_suffix_lookup_still_finds_the_real_test(tmp_path):
    """POSITIVE CONTROL for the escaping above."""
    store = MemoryStore(_config(tmp_path))
    store.save(_record("/api/users", test_id="generated/test_users.py::test_get"))

    assert len(store.get_for_test_suffix("test_users.py::test_get")) == 1
    store.close()


def test_escape_like_leaves_ordinary_text_alone():
    assert escape_like("api/orders") == "api/orders"
    assert escape_like("user_profile") == "user\\_profile"
    assert escape_like("100%") == "100\\%"
