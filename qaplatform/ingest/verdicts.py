"""``verdicts.jsonl`` lines (or a JSON array) -> verdict-row payloads, tolerantly.

A malformed line or a row that breaks the schema is counted and dropped, never raised:
the same tolerance the local verdict reader applies, so one bad line in a long file does
not lose the rest.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from qaplatform import schemas


def accept_row(obj: Any) -> dict[str, Any] | None:
    """The normalised row (``test_id`` filled from ``nodeid``), or ``None`` when invalid."""
    if not isinstance(obj, dict) or schemas.errors_for("verdict-row", obj):
        return None
    row = dict(obj)
    row["test_id"] = row.get("test_id") or row.get("nodeid")
    return row


def parse_verdict_lines(lines: Iterable[str]) -> tuple[list[dict[str, Any]], int]:
    """``(rows, dropped)`` from JSONL text lines; blank lines are ignored."""
    rows: list[dict[str, Any]] = []
    dropped = 0
    for line in lines:
        if not line.strip():
            continue
        try:
            row = accept_row(json.loads(line))
        except ValueError:
            row = None
        if row is None:
            dropped += 1
        else:
            rows.append(row)
    return rows, dropped


__all__ = ["accept_row", "parse_verdict_lines"]
