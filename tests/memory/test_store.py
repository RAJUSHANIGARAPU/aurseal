"""Tests for memory.store.MemoryStore."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from memory.config import MemoryConfig
from memory.models import MemoryRecord
from memory.store import MemoryStore, _endpoint_stem


def _config(tmp_path: Path) -> MemoryConfig:
    return MemoryConfig(
        enabled=True,
        db_path=tmp_path / "test_memory.db",
        ttl_days=90,
        max_records_per_test=5,
        similarity_top_k=3,
    )


def _record(
    test_id: str = "test_foo",
    endpoint: str = "/api/users",
    category: str = "ASSERTION_ERROR",
    fix_outcome: str = "pending",
    days_old: int = 0,
) -> MemoryRecord:
    ts = (datetime.now(tz=timezone.utc) - timedelta(days=days_old)).isoformat()
    return MemoryRecord(
        id=uuid.uuid4().hex,
        test_id=test_id,
        endpoint=endpoint,
        method="GET",
        category=category,
        error_signature="AssertionError: expected 200 got 404",
        root_cause="endpoint not found",
        fix_strategy="check base url",
        fix_outcome=fix_outcome,
        environment="qa",
        run_id="run_001",
        timestamp=ts,
        ttl_days=90,
    )


# ------------------------------------------------------------------
# Init & schema
# ------------------------------------------------------------------


def test_init_creates_db_file(tmp_path):
    cfg = _config(tmp_path)
    store = MemoryStore(cfg)
    assert cfg.db_path.exists()
    store.close()


def test_init_creates_tables(tmp_path):
    cfg = _config(tmp_path)
    store = MemoryStore(cfg)
    cur = store._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cur.fetchall()}
    assert "memory_records" in tables
    store.close()


def test_init_creates_parent_dirs(tmp_path):
    cfg = _config(tmp_path)
    cfg.db_path = tmp_path / "nested" / "dir" / "memory.db"
    store = MemoryStore(cfg)
    assert cfg.db_path.exists()
    store.close()


# ------------------------------------------------------------------
# Save & retrieve
# ------------------------------------------------------------------


def test_save_and_count(tmp_path):
    store = MemoryStore(_config(tmp_path))
    store.save(_record())
    assert store.count() == 1
    store.close()


def test_save_multiple_records(tmp_path):
    store = MemoryStore(_config(tmp_path))
    for i in range(3):
        store.save(_record(test_id=f"test_{i}"))
    assert store.count() == 3
    store.close()


def test_get_for_test_returns_records(tmp_path):
    store = MemoryStore(_config(tmp_path))
    store.save(_record(test_id="test_alpha"))
    store.save(_record(test_id="test_beta"))
    results = store.get_for_test("test_alpha")
    assert len(results) == 1
    assert results[0].test_id == "test_alpha"
    store.close()


def test_get_for_test_respects_limit(tmp_path):
    store = MemoryStore(_config(tmp_path))
    for _ in range(4):
        store.save(_record(test_id="test_x"))
    results = store.get_for_test("test_x", limit=2)
    assert len(results) == 2
    store.close()


def test_get_for_test_returns_newest_first(tmp_path):
    store = MemoryStore(_config(tmp_path))
    for days in [5, 3, 1]:
        store.save(_record(test_id="test_order", days_old=days))
    results = store.get_for_test("test_order")
    # Newest first: days_old=1 should be first
    assert results[0].timestamp > results[1].timestamp
    store.close()


# ------------------------------------------------------------------
# Query
# ------------------------------------------------------------------


def test_query_by_endpoint_returns_match(tmp_path):
    store = MemoryStore(_config(tmp_path))
    store.save(_record(endpoint="/api/users/123"))
    results = store.query(endpoint="/api/users/456")  # stem matches /api/users
    assert len(results) == 1
    store.close()


def test_query_by_category_filters(tmp_path):
    store = MemoryStore(_config(tmp_path))
    store.save(_record(category="ASSERTION_ERROR"))
    store.save(_record(category="TIMEOUT_ERROR"))
    results = store.query(category="ASSERTION_ERROR")
    assert all(r.category == "ASSERTION_ERROR" for r in results)
    store.close()


def test_query_returns_empty_when_no_match(tmp_path):
    store = MemoryStore(_config(tmp_path))
    store.save(_record(endpoint="/api/orders"))
    results = store.query(endpoint="/completely/different/path")
    assert results == []
    store.close()


# ------------------------------------------------------------------
# update_outcome
# ------------------------------------------------------------------


def test_update_outcome_marks_pending_as_resolved(tmp_path):
    store = MemoryStore(_config(tmp_path))
    store.save(_record(test_id="test_r", fix_outcome="pending"))
    store.update_outcome("test_r", "resolved")
    results = store.get_for_test("test_r")
    assert results[0].fix_outcome == "resolved"
    store.close()


def test_update_outcome_does_not_touch_non_pending(tmp_path):
    store = MemoryStore(_config(tmp_path))
    store.save(_record(test_id="test_s", fix_outcome="unresolved"))
    store.update_outcome("test_s", "resolved")
    results = store.get_for_test("test_s")
    assert results[0].fix_outcome == "unresolved"
    store.close()


# ------------------------------------------------------------------
# Prune & max-per-test enforcement
# ------------------------------------------------------------------


def test_prune_removes_expired_records(tmp_path):
    cfg = _config(tmp_path)
    cfg.ttl_days = 1
    store = MemoryStore(cfg)
    old = _record(days_old=5)
    old.ttl_days = 1
    store.save(old)
    store.save(_record(days_old=0))
    pruned = store.prune_expired()
    assert pruned == 1
    assert store.count() == 1
    store.close()


def test_prune_keeps_fresh_records(tmp_path):
    store = MemoryStore(_config(tmp_path))
    store.save(_record(days_old=0))
    pruned = store.prune_expired()
    assert pruned == 0
    assert store.count() == 1
    store.close()


def test_max_records_per_test_enforced(tmp_path):
    cfg = _config(tmp_path)
    cfg.max_records_per_test = 3
    store = MemoryStore(cfg)
    for _ in range(6):
        store.save(_record(test_id="test_capped"))
    results = store.get_for_test("test_capped")
    assert len(results) <= 3
    store.close()


# ------------------------------------------------------------------
# Endpoint stem helper
# ------------------------------------------------------------------


def test_endpoint_stem_strips_query_params():
    assert "?" not in _endpoint_stem("/api/users?page=2")


def test_endpoint_stem_strips_numeric_segments():
    stem = _endpoint_stem("/api/users/42/orders")
    assert "42" not in stem


def test_endpoint_stem_strips_uuids():
    stem = _endpoint_stem("/api/users/550e8400-e29b-41d4-a716-446655440000")
    assert "550e8400" not in stem
