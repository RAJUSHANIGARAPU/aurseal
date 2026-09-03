"""The store runs under an eight-way pool and used to lose about a quarter of it.

One shared sqlite3.Connection driven from eight threads. Measured before the
fix, 400 saves through ThreadPoolExecutor(max_workers=8): 307 / 310 / 318 rows
persisted across three runs, against a single-threaded control of 400/400. The
underlying InterfaceError and "cannot commit - no transaction is active" were
logged at DEBUG, so the store reported success while dropping records.

Deterministic by construction: threads are started and joined, nothing sleeps,
and the assertion is on the row count rather than on timing.
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from memory.config import MemoryConfig
from memory.models import MemoryRecord
from memory.store import MemoryStore

_THREADS = 8
_PER_THREAD = 50
_TOTAL = _THREADS * _PER_THREAD


def _config(tmp_path: Path, cap: int = 1000) -> MemoryConfig:
    return MemoryConfig(
        enabled=True,
        db_path=tmp_path / "concurrent.db",
        ttl_days=90,
        max_records_per_test=cap,
        similarity_top_k=3,
    )


def _record(test_id: str) -> MemoryRecord:
    return MemoryRecord(
        id=uuid.uuid4().hex,
        test_id=test_id,
        endpoint="/api/orders",
        method="POST",
        category="ENV_ERROR",
        error_signature="connection reset",
        root_cause="connection reset",
        fix_strategy="",
        fix_outcome="pending",
        environment="qa",
        run_id="run",
        timestamp=datetime.now(tz=timezone.utc).isoformat(),
        ttl_days=90,
    )


def _save_concurrently(store: MemoryStore, test_id_for) -> None:
    """Run _PER_THREAD saves on each of _THREADS threads, then join them all."""
    barrier = threading.Barrier(_THREADS)

    def worker(worker_index: int) -> None:
        # The barrier makes every thread hit the store at the same moment
        # rather than relying on scheduling luck to produce contention.
        barrier.wait()
        for i in range(_PER_THREAD):
            store.save(_record(test_id_for(worker_index, i)))

    threads = [threading.Thread(target=worker, args=(w,)) for w in range(_THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


def test_every_concurrent_save_persists(tmp_path):
    store = MemoryStore(_config(tmp_path))

    _save_concurrently(store, lambda w, i: f"tests/test_w{w}.py::test_{i}")

    assert store.count() == _TOTAL
    assert store.save_failures == 0
    store.close()


def test_a_single_threaded_run_persists_the_same_number(tmp_path):
    """POSITIVE CONTROL — the count above is a real bound, not an artefact.

    This is the control the original measurement used: 400/400 single-threaded
    against 307-318 under the pool.
    """
    store = MemoryStore(_config(tmp_path))

    for w in range(_THREADS):
        for i in range(_PER_THREAD):
            store.save(_record(f"tests/test_w{w}.py::test_{i}"))

    assert store.count() == _TOTAL
    assert store.save_failures == 0
    store.close()


def test_the_per_test_cap_is_exact_under_concurrency(tmp_path):
    """_enforce_max_per_test raced: with cap=20 the counts came out 20, 22, 20, 21, 20, 23."""
    cap = 20
    store = MemoryStore(_config(tmp_path, cap=cap))

    _save_concurrently(store, lambda w, i: "tests/test_hot.py::test_one")

    assert store.count() == cap
    assert store.save_failures == 0
    store.close()


def test_records_saved_from_other_threads_are_readable_and_correct(tmp_path):
    """POSITIVE CONTROL — persisting rows is not enough if they come back wrong."""
    store = MemoryStore(_config(tmp_path))

    _save_concurrently(store, lambda w, i: f"tests/test_w{w}.py::test_{i}")

    found = store.query(endpoint="/api/orders", limit=_TOTAL)
    assert len(found) == _TOTAL
    assert {r.endpoint for r in found} == {"/api/orders"}
    assert len({r.id for r in found}) == _TOTAL
    store.close()


def test_a_failed_save_is_counted_and_logged_at_error(tmp_path, caplog):
    """A swallowed write is data loss. DEBUG hid 82-93 lost rows per 400 saves."""
    store = MemoryStore(_config(tmp_path))
    broken = _record("tests/test_x.py::test_x")
    broken.metadata = {"unserialisable": object()}

    with caplog.at_level(logging.ERROR, logger="memory.store"):
        store.save(broken)

    assert store.save_failures == 1
    assert store.count() == 0
    assert any(broken.id in record.getMessage() for record in caplog.records)
    store.close()
