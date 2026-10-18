"""Shared helpers for the results tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path


def rows(db: Path, sql: str, *params) -> list[dict]:
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()
