"""Plugin storage layer — SQLite persistence for plugin runs, results, and entities."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from plugins._base_plugin import PluginResult

logger = logging.getLogger(__name__)

_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS test_run_summary (
    id          TEXT PRIMARY KEY,
    health_score INTEGER NOT NULL,
    plugins_run  TEXT NOT NULL,
    cost_usd     REAL NOT NULL,
    timestamp    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plugin_result (
    id           TEXT PRIMARY KEY,
    run_id       TEXT NOT NULL,
    plugin_name  TEXT NOT NULL,
    status       TEXT NOT NULL,
    findings_json TEXT NOT NULL,
    duration_ms  REAL NOT NULL,
    cost_usd     REAL NOT NULL,
    timestamp    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS finding (
    id            TEXT PRIMARY KEY,
    result_id     TEXT NOT NULL,
    severity      TEXT NOT NULL,
    title         TEXT NOT NULL,
    detail        TEXT NOT NULL,
    file_path     TEXT DEFAULT '',
    fix_suggestion TEXT DEFAULT '',
    timestamp     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cost_record (
    id          TEXT PRIMARY KEY,
    model       TEXT NOT NULL,
    tokens      INTEGER NOT NULL,
    cost_usd    REAL NOT NULL,
    plugin_name TEXT NOT NULL,
    timestamp   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS edge_case_node (
    id                  TEXT PRIMARY KEY,
    profile_description TEXT NOT NULL,
    failure_detail      TEXT DEFAULT '',
    plugin_name         TEXT NOT NULL,
    timestamp           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS golden_snapshot (
    id              TEXT PRIMARY KEY,
    function_name   TEXT NOT NULL,
    input_hash      TEXT NOT NULL,
    output_snapshot TEXT NOT NULL,
    timestamp       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS compliance_check (
    id              TEXT PRIMARY KEY,
    rule_text       TEXT NOT NULL,
    status          TEXT NOT NULL,
    checked_at      TEXT NOT NULL,
    violation_detail TEXT DEFAULT ''
);
"""


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _uid() -> str:
    import uuid

    return str(uuid.uuid4())


class PluginStorage:
    """SQLite-backed storage for plugin run data."""

    def __init__(self, db_path: Path = Path("reports/plugin_runs.db")) -> None:
        self._db_path = db_path
        self._conn = self._connect()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_db(self) -> None:
        for stmt in _CREATE_TABLES.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                self._conn.execute(stmt)
        self._conn.commit()

    def save_run(
        self, run_id: str, health_score: int, plugins_run: list[str], cost_usd: float
    ) -> None:
        try:
            self._conn.execute(
                "INSERT OR REPLACE INTO test_run_summary VALUES (?,?,?,?,?)",
                (run_id, health_score, json.dumps(plugins_run), cost_usd, _now()),
            )
            self._conn.commit()
        except Exception:  # noqa: BLE001
            logger.debug("PluginStorage.save_run failed", exc_info=True)

    def save_plugin_result(self, run_id: str, plugin_name: str, result: PluginResult) -> None:
        result_id = _uid()
        try:
            self._conn.execute(
                "INSERT OR REPLACE INTO plugin_result VALUES (?,?,?,?,?,?,?,?)",
                (
                    result_id,
                    run_id,
                    plugin_name,
                    result.status,
                    json.dumps(result.findings),
                    result.duration_ms,
                    result.cost_usd,
                    _now(),
                ),
            )
            self._conn.commit()
        except Exception:  # noqa: BLE001
            logger.debug("PluginStorage.save_plugin_result failed", exc_info=True)

    def get_recent_runs(self, n: int = 4) -> list[dict]:
        try:
            cur = self._conn.execute(
                "SELECT id, health_score, plugins_run, cost_usd, timestamp "
                "FROM test_run_summary ORDER BY timestamp DESC LIMIT ?",
                (n,),
            )
            rows = cur.fetchall()
            return [
                {
                    "id": row[0],
                    "health_score": row[1],
                    "plugins_run": json.loads(row[2]),
                    "cost_usd": row[3],
                    "timestamp": row[4],
                }
                for row in rows
            ]
        except Exception:  # noqa: BLE001
            logger.debug("PluginStorage.get_recent_runs failed", exc_info=True)
            return []

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:  # noqa: BLE001
            pass
