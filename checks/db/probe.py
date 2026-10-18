"""Read-only database probes: run named queries, take snapshots, never write by accident.

``SqliteProbe`` (stdlib) opens the file with ``mode=ro``, so read-only is enforced
by the engine, and still runs the statement guard first. ``SqlAlchemyProbe``
(optional dependency, imported lazily) covers every other engine: statement guard,
every query in a transaction that is always rolled back, and a read-only
transaction where the dialect has one. The real guarantee on a shared database is a
read-only database user.

Writes (fixture setup) need :meth:`DbProbe.writer` with ``allow_writes=True`` and
``DB_WRITES_ALLOWED=true``; every write with an undo statement is recorded in a
cleanup ledger.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from checks.db.guard import require_read_only

WRITES_ENV = "DB_WRITES_ALLOWED"

#: name -> SQL, or name -> (SQL, params). Parameters are bound, never interpolated.
QuerySet = Mapping[str, str | tuple[str, Mapping[str, Any]]]


class WritesNotAllowed(PermissionError):
    pass


class DbProbe(Protocol):
    def query(self, sql: str, params: Mapping[str, Any] | None = None) -> list[dict]: ...

    def snapshot(
        self, qs: QuerySet, params: Mapping[str, Any] | None = None
    ) -> dict[str, list[dict]]: ...


def _split(entry: str | tuple[str, Mapping[str, Any]]) -> tuple[str, Mapping[str, Any]]:
    if isinstance(entry, tuple):
        return entry[0], dict(entry[1])
    return entry, {}


class _SnapshotMixin:
    def query(self, sql: str, params: Mapping[str, Any] | None = None) -> list[dict]:
        raise NotImplementedError

    def snapshot(
        self, qs: QuerySet, params: Mapping[str, Any] | None = None
    ) -> dict[str, list[dict]]:
        """``{name: rows}`` — the before/after shape ``verification.invariants`` evaluates."""
        shared = dict(params or {})
        out: dict[str, list[dict]] = {}
        for name, entry in qs.items():
            sql, own = _split(entry)
            out[name] = self.query(sql, {**shared, **own})
        return out


def _writes_allowed(env: Mapping[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    return (env.get(WRITES_ENV) or "").strip().lower() in ("1", "true", "yes", "on")


class DbWriter:
    """Explicit write access, for fixture setup only. Undo statements go to the ledger."""

    def __init__(self, execute, ledger=None) -> None:
        self._execute = execute
        self._ledger = ledger

    def execute(
        self,
        sql: str,
        params: Mapping[str, Any] | None = None,
        *,
        undo: tuple[str, Mapping[str, Any]] | None = None,
        kind: str = "db",
        ident: str | None = None,
    ) -> int:
        count = self._execute(sql, dict(params or {}))
        if undo is not None and self._ledger is not None:
            undo_sql, undo_params = undo
            self._ledger.record(
                kind,
                ident or f"{' '.join(sql.split())[:60]} {dict(params or {})}",
                lambda: self._execute(undo_sql, dict(undo_params)),
            )
        return count


def _refuse_writer(allow_writes: bool, env: Mapping[str, str] | None) -> None:
    if not allow_writes:
        raise WritesNotAllowed("writer() needs allow_writes=True")
    if not _writes_allowed(env):
        raise WritesNotAllowed(f"{WRITES_ENV} is not true; database writes are refused")


class SqliteProbe(_SnapshotMixin):
    """Read-only probe over a SQLite file (``mode=ro``: the engine refuses writes)."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if not self.path.is_file():
            raise FileNotFoundError(f"no SQLite database at {self.path}")
        self._conn = sqlite3.connect(
            f"{self.path.resolve().as_uri()}?mode=ro", uri=True, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row

    def query(self, sql: str, params: Mapping[str, Any] | None = None) -> list[dict]:
        require_read_only(sql)
        cursor = self._conn.execute(sql, dict(params or {}))
        try:
            return [dict(row) for row in cursor.fetchall()]
        finally:
            cursor.close()

    def writer(
        self, allow_writes: bool = False, ledger=None, env: Mapping[str, str] | None = None
    ) -> DbWriter:
        _refuse_writer(allow_writes, env)
        conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)

        def execute(sql: str, params: Mapping[str, Any]) -> int:
            return conn.execute(sql, dict(params)).rowcount

        return DbWriter(execute, ledger)

    def close(self) -> None:
        self._conn.close()


class SqlAlchemyProbe(_SnapshotMixin):
    """Any SQLAlchemy URL. Optional dependency (``requirements-checks.txt``), lazily imported."""

    def __init__(self, url: str, *, read_only: bool = True) -> None:
        try:
            import sqlalchemy
        except ImportError as exc:  # pragma: no cover - exercised only without the extra
            raise ImportError(
                "SqlAlchemyProbe needs SQLAlchemy (pip install -r requirements-checks.txt)"
            ) from exc
        self._sa = sqlalchemy
        self.url = url
        self.read_only = read_only
        self._engine = sqlalchemy.create_engine(url)

    def query(self, sql: str, params: Mapping[str, Any] | None = None) -> list[dict]:
        if self.read_only:
            require_read_only(sql)
        with self._engine.connect() as conn:
            transaction = conn.begin()
            try:
                if self.read_only and conn.dialect.name in ("postgresql", "mysql", "mariadb"):
                    conn.exec_driver_sql("SET TRANSACTION READ ONLY")
                result = conn.execute(self._sa.text(sql), dict(params or {}))
                return [dict(row) for row in result.mappings().all()]
            finally:
                transaction.rollback()  # a read never commits anything

    def writer(
        self, allow_writes: bool = False, ledger=None, env: Mapping[str, str] | None = None
    ) -> DbWriter:
        _refuse_writer(allow_writes, env)

        def execute(sql: str, params: Mapping[str, Any]) -> int:
            with self._engine.begin() as conn:
                return conn.execute(self._sa.text(sql), dict(params)).rowcount

        return DbWriter(execute, ledger)

    def close(self) -> None:
        self._engine.dispose()


def open_probe(url: str) -> SqliteProbe | SqlAlchemyProbe:
    """``sqlite:///path`` (stdlib) or any other SQLAlchemy URL (needs the extra)."""
    if url.startswith("sqlite:///"):
        return SqliteProbe(url[len("sqlite:///") :])
    if url.startswith("sqlite://"):
        raise ValueError("an in-memory SQLite URL has nothing to probe; give a file path")
    return SqlAlchemyProbe(url)
