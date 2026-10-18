"""Small shared helpers: UTC timestamps, JSON columns, paths and settings."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DB_ENV = "RESULTS_DB"
DIR_ENV = "RESULTS_DIR"
ARTIFACT_ROOT_ENV = "RESULTS_ARTIFACT_ROOT"
DEFAULT_DIR = Path("reports") / "results"
DB_NAME = "results.db"

MESSAGE_LIMIT = 2 * 1024
LONGREPR_LIMIT = 16 * 1024


def utc_now() -> str:
    """ISO-8601 UTC with a ``Z`` suffix, millisecond precision, sortable as text."""
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def to_utc(value: str | None) -> str | None:
    """Normalise an ISO-8601 timestamp to the store's UTC text form; None stays None."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def from_epoch(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    moment = datetime.fromtimestamp(seconds, tz=timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def dumps(value: Any) -> str | None:
    """A ``*_json`` column value: compact, key-sorted; None for None."""
    if value is None:
        return None
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def loads(value: str | None, default: Any = None) -> Any:
    if value is None or value == "":
        return default
    try:
        return json.loads(value)
    except ValueError:
        return default


def truncate(text: str | None, limit: int) -> str | None:
    """Cut ``text`` to ``limit`` UTF-8 bytes, marking the cut; None stays None."""
    if text is None:
        return None
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text
    marker = "\n…[truncated]"
    keep = limit - len(marker.encode("utf-8"))
    return raw[:keep].decode("utf-8", errors="ignore") + marker


def results_dir() -> Path:
    return Path(os.environ.get(DIR_ENV) or DEFAULT_DIR)


def default_db_path() -> Path:
    raw = os.environ.get(DB_ENV, "").strip()
    return Path(raw) if raw else results_dir() / DB_NAME
