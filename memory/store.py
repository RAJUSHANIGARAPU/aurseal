"""SQLite-backed persistent store for test execution memories.

Design choices:
- SQLite (stdlib) — zero extra dependencies
- WAL mode — concurrent readers don't block writer
- One connection per thread, plus a process-wide write lock (see below)
- Non-fatal writes — a save failure is logged at ERROR and counted, not raised
- Enforces max_records_per_test to prevent unbounded growth per test

**Why per-thread connections.** A single ``sqlite3.Connection`` was shared
across the orchestrator's eight-way pool with ``check_same_thread=False``, on
the assumption that SQLite serialises writes internally. It serialises writes
to the *file*; it does not make one connection object safe to drive from eight
threads. Measured, 400 saves through ``ThreadPoolExecutor(max_workers=8)``:
307 / 310 / 318 rows persisted across three runs — 82 to 93 records lost
against a single-threaded control of 400/400. The exceptions were
``sqlite3.InterfaceError: bad parameter or other API misuse`` and
``OperationalError: cannot commit - no transaction is active``, and every one
of them was swallowed at DEBUG, so the loss was silent. ``_enforce_max_per_test``
raced the same way: with ``cap=20`` the per-test counts came out 20, 22, 20, 21,
20, 23, 20, 22 — the bound was not a bound.

**Why the write lock on top.** Per-thread connections fix the API misuse but
leave concurrent writers contending for the database lock. Serialising writes
in-process keeps ``insert -> commit -> enforce cap`` atomic as a unit, which is
what makes the cap exact rather than approximately right.

**Why saves are still non-fatal.** The orchestrator treats memory as
additive — a store outage must not fail a test run. But a swallowed write is
data loss, so failures are logged at ERROR with the record id and counted on
``save_failures``, which callers and tests can assert is zero.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from dataclasses import replace as _dataclass_replace
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from memory.models import MemoryRecord
from memory.normalize import (
    LIKE_ESCAPE_CHAR,
    escape_like,
    is_discriminating,
    normalize_endpoint,
)

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

def _endpoint_stem(url: str) -> str:
    """Canonical match key for a URL — the same one the write path stores."""
    return normalize_endpoint(url).strip("/")


class MemoryStore:
    """Persistent store backed by SQLite.

    Instantiated once per process. Safe under the orchestrator's thread pool:
    each thread gets its own connection and writes take an in-process lock.
    """

    def __init__(self, config: MemoryConfig) -> None:
        self._config = config
        self._db_path = config.db_path
        self._local = threading.local()
        self._conns: list[sqlite3.Connection] = []
        self._conns_lock = threading.Lock()
        self._write_lock = threading.Lock()
        #: Records this store failed to persist. Should stay 0 — a non-zero
        #: value means memory lost data, which is exactly what went unnoticed
        #: while 82-93 of every 400 concurrent saves vanished at DEBUG level.
        self.save_failures = 0
        self._init_db()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def save(self, record: MemoryRecord) -> None:
        """Persist a record. Non-fatal on error, but never silent about it."""
        try:
            # Normalised here, not only in the summarizer, so the key a record
            # is stored under cannot depend on which caller built it. query()
            # applies the identical function; that identity is the whole fix
            # for /api/users/42/orders being unreachable once written.
            row = _dataclass_replace(record, endpoint=normalize_endpoint(record.endpoint)).to_row()
            with self._write_lock:
                conn = self._conn
                conn.execute(
                    "INSERT OR REPLACE INTO memory_records "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    row,
                )
                conn.commit()
                self._enforce_max_per_test(record.test_id)
        except Exception:  # noqa: BLE001
            # ERROR, not DEBUG. At DEBUG this line hid 82-93 lost records per
            # 400 concurrent saves and the store still reported success.
            # The lock matters: `+= 1` is not atomic, and a counter that
            # under-reports losses is the same false green in miniature.
            with self._write_lock:
                self.save_failures += 1
            logger.error(
                "MemoryStore.save failed for record %s (test %s) — record lost",
                record.id,
                record.test_id,
                exc_info=True,
            )

    def query(
        self,
        endpoint: str = "",
        category: str = "",
        limit: int = 50,
        *,
        allow_unfiltered: bool = False,
    ) -> list[MemoryRecord]:
        """Return candidate records for similarity ranking.

        Filters by endpoint stem (broad) and optionally by category.
        Does NOT do semantic search — that is the retriever's job.

        An endpoint that is empty, or one that carries nothing but an id
        (``/123``), used to add no SQL condition at all — so the "narrow to
        this endpoint" call returned the whole table. That path is routine, not
        exotic: the middleware analyses every hand-written and UI test with
        ``request=None``. Three resolved failures on /api/billing, /api/auth
        and /api/search then came back against "connection reset while creating
        an order" at confidence 1.0, offering "restart the billing worker" as
        the fix.

        So an unusable endpoint now returns nothing. Wanting the unfiltered set
        is legitimate but has to be said out loud: pass ``allow_unfiltered``.
        """
        expiry = self._expiry_ts()
        conditions = ["timestamp > ?"]
        params: list = [expiry]

        stem = _endpoint_stem(endpoint) if endpoint else ""
        if stem and is_discriminating(stem):
            # ESCAPE is not optional here: '_' is a single-character wildcard,
            # and querying /api/user_profile matched a stored /api/userxprofile.
            conditions.append(f"endpoint LIKE ? ESCAPE '{LIKE_ESCAPE_CHAR}'")
            params.append(f"%{escape_like(stem)}%")
        elif not allow_unfiltered:
            logger.debug(
                "[memory] no usable endpoint filter for %r — returning no candidates", endpoint
            )
            return []

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

    def get_for_test_suffix(self, suffix: str, limit: int | None = None) -> list[MemoryRecord]:
        """Return records for a test identified by its ``file.py::function`` tail.

        Records are keyed by the full pytest nodeid, but callers that only hold
        a Postman request cannot reconstruct the directory in front of it — that
        comes from the orchestrator's ``output_dir``. Matching on a path
        boundary keeps this exact: ``%/test_users.py::test_get`` cannot be
        satisfied by ``test_other_users.py::test_get``.

        The suffix is LIKE-escaped for the same reason the endpoint stem is:
        an unescaped ``_`` matches any character, and test filenames are almost
        entirely underscores — ``test_users.py::test_get`` would have matched
        ``test-users.py::test.get`` and any other one-character substitution.
        """
        if not suffix:
            return []
        cap = limit or self._config.max_records_per_test
        expiry = self._expiry_ts()
        try:
            cur = self._conn.execute(
                f"""SELECT * FROM memory_records
                   WHERE (test_id = ? OR test_id LIKE ? ESCAPE '{LIKE_ESCAPE_CHAR}')
                     AND timestamp > ?
                   ORDER BY timestamp DESC LIMIT ?""",
                (suffix, f"%/{escape_like(suffix)}", expiry, cap),
            )
            return [MemoryRecord.from_row(row) for row in cur.fetchall()]
        except Exception:  # noqa: BLE001
            logger.debug("MemoryStore.get_for_test_suffix failed", exc_info=True)
            return []

    def update_outcome(self, test_id: str, outcome: str) -> None:
        """Update all pending records for test_id to the given outcome."""
        try:
            sql = (
                "UPDATE memory_records SET fix_outcome = ?"
                " WHERE test_id = ? AND fix_outcome = 'pending'"
            )
            with self._write_lock:
                conn = self._conn
                conn.execute(sql, (outcome, test_id))
                conn.commit()
        except Exception:  # noqa: BLE001
            logger.error("MemoryStore.update_outcome failed for %s", test_id, exc_info=True)

    def prune_expired(self) -> int:
        """Delete records past their individual TTL. Returns count deleted."""
        try:
            with self._write_lock:
                conn = self._conn
                cur = conn.execute(
                    "DELETE FROM memory_records "
                    "WHERE datetime(timestamp, '+' || ttl_days || ' days') < datetime('now')"
                )
                conn.commit()
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
        """Close every connection this store opened, on whichever thread opened it."""
        with self._conns_lock:
            conns, self._conns = self._conns, []
        self._local = threading.local()
        for conn in conns:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @property
    def _conn(self) -> sqlite3.Connection:
        """This thread's connection, opened on first use.

        A single shared connection driven from the eight-way pool raised
        InterfaceError / "cannot commit - no transaction is active" and dropped
        roughly a quarter of all writes. One connection per thread is the
        supported way to use sqlite3 concurrently.
        """
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._connect()
            self._local.conn = conn
            with self._conns_lock:
                self._conns.append(conn)
        return conn

    def _connect(self) -> sqlite3.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread stays False only so close() can reap connections
        # opened by worker threads; no connection is ever used from two threads.
        # timeout gives a writer that loses the file lock room to retry instead
        # of surfacing "database is locked" as a lost record.
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_db(self) -> None:
        conn = self._conn
        conn.execute(_CREATE_TABLE)
        for idx in _CREATE_INDICES:
            conn.execute(idx)
        conn.commit()

    def _expiry_ts(self) -> str:
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=self._config.ttl_days)
        return cutoff.isoformat()

    def _enforce_max_per_test(self, test_id: str) -> None:
        """Trim a test's history to the cap. Caller must hold ``_write_lock``.

        Under the shared connection this raced: with ``cap=20`` the observed
        per-test counts were 20, 22, 20, 21, 20, 23, 20, 22. Running it inside
        the same lock as the insert makes the bound exact, and a failure is
        logged rather than dropped on the floor by a bare ``except: pass``.
        """
        cap = self._config.max_records_per_test
        try:
            conn = self._conn
            conn.execute(
                """DELETE FROM memory_records WHERE test_id = ? AND id NOT IN (
                       SELECT id FROM memory_records WHERE test_id = ?
                       ORDER BY timestamp DESC LIMIT ?
                   )""",
                (test_id, test_id, cap),
            )
            conn.commit()
        except Exception:  # noqa: BLE001
            logger.error(
                "MemoryStore could not enforce the %d-record cap for %s", cap, test_id,
                exc_info=True,
            )
