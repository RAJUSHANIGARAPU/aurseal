"""SQLite persistence.

Each resource is a table with a few real columns (the ones a read-only probe or a test
would filter on) plus a ``data`` column holding the full JSON document. Writes go through
:class:`Store`, which keeps the key columns in sync with the document.

The database is a plain file, so a read-only probe can open it with
``sqlite3.connect("file:<path>?mode=ro", uri=True)``.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable
from pathlib import Path
from typing import Any

#: table -> key columns copied out of the JSON document on every write.
TABLES: dict[str, tuple[str, ...]] = {
    "customers": ("number", "status", "run_tag"),
    "reseller_terms": ("name", "run_tag"),
    "partners": ("number", "run_tag"),
    "plans": ("code", "kind", "run_tag"),
    "subscriptions": ("number", "status", "customer_id", "partner_id", "run_tag"),
    "versions": ("subscription_id", "version", "effective_from"),
    "drafts": ("subscription_id", "kind", "status"),
    "notes": ("subscription_id",),
    "transactions": ("subscription_id", "kind", "state", "period_start", "job_id"),
    "invoices": ("subscription_id", "transaction_id", "number", "status"),
    "journal": ("invoice_id", "reference"),
    "payments": ("invoice_id",),
    "accounts": ("number", "managed"),
    "settlements": ("partner_id", "status"),
    "jobs": ("kind", "state", "due_date"),
    "documents": ("subscription_id", "template", "file_id"),
    "files": ("name", "content_type"),
    "messages": ("message_id", "subscription_id", "type", "status"),
    "tasks": ("subscription_id", "state"),
    "users": ("username",),
    "role_events": ("user_id", "type"),
    "integrations": ("service",),
    "exports": ("kind", "status"),
    "audit": ("method", "path", "entity_type", "entity_id"),
    "events": ("kind", "subscription_id"),
    "tokens": ("token",),
}


def _schema(table: str, columns: Iterable[str]) -> str:
    cols = "".join(f", {c} TEXT" for c in columns)
    return (
        f"CREATE TABLE IF NOT EXISTS {table} "
        f"(id INTEGER PRIMARY KEY AUTOINCREMENT{cols}, data TEXT NOT NULL)"
    )


def _column_value(value: Any) -> Any:
    if value is None or isinstance(value, (int, float, str)):
        return value if not isinstance(value, bool) else str(value).lower()
    return json.dumps(value, sort_keys=True)


class Store:
    """Small document store over one SQLite connection, guarded by a re-entrant lock."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self.lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("CREATE TABLE IF NOT EXISTS blobs (id INTEGER PRIMARY KEY, body BLOB)")
        for table, cols in TABLES.items():
            self._conn.execute(_schema(table, cols))

    def close(self) -> None:
        with self.lock:
            self._conn.close()

    def wipe(self) -> None:
        with self.lock:
            for table in (*TABLES, "blobs"):
                self._conn.execute(f"DELETE FROM {table}")
            self._conn.execute("DELETE FROM sqlite_sequence")

    # -- documents --------------------------------------------------------

    def insert(self, table: str, doc: dict[str, Any]) -> dict[str, Any]:
        cols = TABLES[table]
        with self.lock:
            cur = self._conn.execute(
                f"INSERT INTO {table} ({', '.join((*cols, 'data'))}) "
                f"VALUES ({', '.join('?' * (len(cols) + 1))})",
                [*(_column_value(doc.get(c)) for c in cols), "{}"],
            )
            doc = {"id": cur.lastrowid, **{k: v for k, v in doc.items() if k != "id"}}
            self._write(table, doc)
        return doc

    def save(self, table: str, doc: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            self._write(table, doc)
        return doc

    def _write(self, table: str, doc: dict[str, Any]) -> None:
        cols = TABLES[table]
        sets = ", ".join(f"{c} = ?" for c in (*cols, "data"))
        self._conn.execute(
            f"UPDATE {table} SET {sets} WHERE id = ?",
            [*(_column_value(doc.get(c)) for c in cols), json.dumps(doc), doc["id"]],
        )

    def get(self, table: str, doc_id: Any) -> dict[str, Any] | None:
        try:
            key = int(doc_id)
        except (TypeError, ValueError):
            return None
        with self.lock:
            row = self._conn.execute(f"SELECT data FROM {table} WHERE id = ?", [key]).fetchone()
        return json.loads(row[0]) if row else None

    def find(
        self,
        table: str,
        order: str = "id",
        limit: int | None = None,
        **where: Any,
    ) -> list[dict[str, Any]]:
        """Documents whose key columns equal the given values, ordered by a key column."""
        clauses = " AND ".join(f"{c} = ?" for c in where)
        sql = f"SELECT data FROM {table}" + (f" WHERE {clauses}" if clauses else "")
        sql += f" ORDER BY {order}" + (f" LIMIT {int(limit)}" if limit else "")
        with self.lock:
            rows = self._conn.execute(sql, [_column_value(v) for v in where.values()]).fetchall()
        return [json.loads(r[0]) for r in rows]

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        """Run a SELECT returning the ``data`` column; used for keyset paging."""
        with self.lock:
            rows = self._conn.execute(sql, list(params)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def delete(self, table: str, doc_id: int) -> None:
        with self.lock:
            self._conn.execute(f"DELETE FROM {table} WHERE id = ?", [doc_id])

    # -- binary ------------------------------------------------------------

    def put_blob(self, body: bytes) -> int:
        with self.lock:
            cur = self._conn.execute("INSERT INTO blobs (body) VALUES (?)", [body])
            return int(cur.lastrowid or 0)

    def get_blob(self, blob_id: int) -> bytes | None:
        with self.lock:
            row = self._conn.execute("SELECT body FROM blobs WHERE id = ?", [blob_id]).fetchone()
        return bytes(row[0]) if row else None
