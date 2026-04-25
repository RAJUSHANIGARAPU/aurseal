"""SQLite-backed persistent store for test execution memories.

Design choices:
- SQLite (stdlib) — zero extra dependencies
- WAL mode — concurrent readers don't block writer
- check_same_thread=False — safe because SQLite serialises writes internally
- Fire-and-forget writes — non-fatal OSError/sqlite3 errors are logged, not raised
- Enforces max_records_per_test to prevent unbounded growth per test
"""

from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from memory.models import MemoryRecord

if TYPE_CHECKING:
    from memory.config import MemoryConfig

logger = logging.getLogger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS memory_records (
    id              TEXT PRIMARY KEY,
    test_id         TEXT NOT NULL,
    endpoint        TEXT DEFAULT '',
    method          TEXT DEFAULT '',
    category        TEXT NOT NULL,
    error_signature TEXT DEFAULT '',
    root_cause      TEXT DEFAULT '',
    fix_strategy    TEXT DEFAULT '',
    fix_outcome     TEXT DEFAULT 'pending',
    environment     TEXT DEFAULT 'qa',
    run_id          TEXT NOT NULL,
    timestamp       TEXT NOT NULL,
    ttl_days        INTEGER DEFAULT 90,
    payload_snippet TEXT DEFAULT '',
    status_code     INTEGER,
    metadata        TEXT DEFAULT '{}'
)
"""

_CREATE_INDICES = (
    "CREATE INDEX IF NOT EXISTS idx_mem_test_id   ON memory_records(test_id)",
    "CREATE INDEX IF NOT EXISTS idx_mem_endpoint  ON memory_records(endpoint)",
    "CREATE INDEX IF NOT EXISTS idx_mem_timestamp ON memory_records(timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_mem_category  ON memory_records(category)",
)

_UUID_RE = re.compile(r"[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}", re.I)
_NUM_SEGMENT_RE = re.compile(r"/\d+")


def _endpoint_stem(url: str) -> str:
    """Strip query params, UUIDs, and numeric path segments for broader matching."""
    url = url.split("?")[0].lower()
    url = _UUID_RE.sub("", url)
    url = _NUM_SEGMENT_RE.sub("", url)
    return url.strip("/")


class MemoryStore:
    """Persistent store backed by SQLite.

    Instantiated once per process. Thread-safe for concurrent reads;
    writes are serialised by SQLite's WAL locking.
    """

    def __init__(self, config: MemoryConfig) -> None:
        self._config = config
        self._db_path = config.db_path
        self._conn = self._connect()
        self._init_db()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def save(self, record: MemoryRecord) -> None:
        """Persist a record. Non-fatal on any error."""
        try:
            self._conn.execute(
                "INSERT OR REPLACE INTO memory_records VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                record.to_row(),
            )
            self._conn.commit()
            self._enforce_max_per_test(record.test_id)
        except Exception:  # noqa: BLE001
            logger.debug("MemoryStore.save non-fatal error", exc_info=True)

    def query(
        self,
        endpoint: str = "",
        category: str = "",
        limit: int = 50,
    ) -> list[MemoryRecord]:
        """Return candidate records for similarity ranking.

        Filters by endpoint stem (broad) and optionally by category.
        Does NOT do semantic search — that is the retriever's job.
        """
        expiry = self._expiry_ts()
        conditions = ["timestamp > ?"]
        params: list = [expiry]

        if endpoint:
            stem = _endpoint_stem(endpoint)
            if stem:
                conditions.append("endpoint LIKE ?")
                params.append(f"%{stem}%")
        if category:
            conditions.append("category = ?")
            params.append(category)

        where = " AND ".join(conditions)
        sql = f"SELECT * FROM memory_records WHERE {where} ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        try:
            cur = self._conn.execute(sql, params)
            return [MemoryRecord.from_row(row) for row in cur.fetchall()]
        except Exception:  # noqa: BLE001
            logger.debug("MemoryStore.query failed", exc_info=True)
            return []

    def get_for_test(self, test_id: str, limit: int | None = None) -> list[MemoryRecord]:
        """Return most recent records for one test, newest first."""
        cap = limit or self._config.max_records_per_test
        expiry = self._expiry_ts()
        try:
            cur = self._conn.execute(
                """SELECT * FROM memory_records
                   WHERE test_id = ? AND timestamp > ?
                   ORDER BY timestamp DESC LIMIT ?""",
                (test_id, expiry, cap),
            )
            return [MemoryRecord.from_row(row) for row in cur.fetchall()]
        except Exception:  # noqa: BLE001
            logger.debug("MemoryStore.get_for_test failed", exc_info=True)
            return []

    def update_outcome(self, test_id: str, outcome: str) -> None:
        """Update all pending records for test_id to the given outcome."""
        try:
            sql = (
                "UPDATE memory_records SET fix_outcome = ?"
                " WHERE test_id = ? AND fix_outcome = 'pending'"
            )
            self._conn.execute(sql, (outcome, test_id))
            self._conn.commit()
        except Exception:  # noqa: BLE001
            logger.debug("MemoryStore.update_outcome failed", exc_info=True)

    def prune_expired(self) -> int:
        """Delete records past their individual TTL. Returns count deleted."""
        try:
            cur = self._conn.execute(
                "DELETE FROM memory_records "
                "WHERE datetime(timestamp, '+' || ttl_days || ' days') < datetime('now')"
            )
            self._conn.commit()
            return cur.rowcount
        except Exception:  # noqa: BLE001
            logger.debug("MemoryStore.prune_expired failed", exc_info=True)
            return 0

    def count(self) -> int:
        try:
            cur = self._conn.execute("SELECT COUNT(*) FROM memory_records")
            row = cur.fetchone()
            return row[0] if row else 0
        except Exception:  # noqa: BLE001
            return 0

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_db(self) -> None:
        self._conn.execute(_CREATE_TABLE)
        for idx in _CREATE_INDICES:
            self._conn.execute(idx)
        self._conn.commit()

    def _expiry_ts(self) -> str:
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=self._config.ttl_days)
        return cutoff.isoformat()

    def _enforce_max_per_test(self, test_id: str) -> None:
        cap = self._config.max_records_per_test
        try:
            self._conn.execute(
                """DELETE FROM memory_records WHERE test_id = ? AND id NOT IN (
                       SELECT id FROM memory_records WHERE test_id = ?
                       ORDER BY timestamp DESC LIMIT ?
                   )""",
                (test_id, test_id, cap),
            )
            self._conn.commit()
        except Exception:  # noqa: BLE001
            pass
