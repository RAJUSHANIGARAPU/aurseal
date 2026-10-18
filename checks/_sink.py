"""Where check metrics go: one row per measurement, keyed by the run id.

Every engine produces rows for a ``metric_<name>`` table and hands them to a
:class:`MetricSink`. Every row carries ``id``, ``run_id`` (``core.run_id``, so it joins
the run's results), an optional ``nodeid`` and ``created_at``. The row shape of each
table is a JSON Schema under ``schemas/metrics/<table>.schema.json``; the sinks validate
against it before writing, so a row another framework pushes and a row this repo writes
obey one contract.

Three sinks exist:

* :class:`JsonlSink` (default): one ``<table>.jsonl`` file per table under
  ``reports/metrics/`` (``METRICS_DIR``). Stdlib plus ``jsonschema``, works offline,
  ships as a CI artefact.
* :class:`MemorySink`: keeps rows in memory (tests, callers that persist elsewhere).
* :class:`StoreSink`: forwards to the results store when it offers
  ``write_metrics(table, rows)``. The engines own nothing about the database.

A sink failure never changes a check's outcome, but it is not silent either:
:func:`write_metrics` returns the reason and the caller reports it.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = REPO_ROOT / "schemas" / "metrics"
DEFAULT_DIR = REPO_ROOT / "reports" / "metrics"
METRICS_DIR_ENV = "METRICS_DIR"
METRICS_SINK_ENV = "METRICS_SINK"
_TABLE_RE = re.compile(r"^metric_[a-z][a-z0-9_]*$")


class MetricSink(Protocol):
    def write(self, table: str, rows: Sequence[Mapping[str, Any]]) -> None: ...


def _check_table(table: str) -> None:
    if not _TABLE_RE.match(table):
        raise ValueError(f"metric table must match metric_<snake_case>, got {table!r}")


def schema_for(table: str) -> dict[str, Any]:
    _check_table(table)
    path = SCHEMA_DIR / f"{table}.schema.json"
    if not path.is_file():
        raise KeyError(f"no schema for metric table {table!r} ({path})")
    return json.loads(path.read_text(encoding="utf-8"))


def validate_row(table: str, row: Mapping[str, Any]) -> None:
    """Raise ``ValueError`` when ``row`` does not match its table's schema."""
    import jsonschema

    try:
        jsonschema.validate(dict(row), schema_for(table))
    except jsonschema.ValidationError as exc:
        raise ValueError(f"{table} row does not match its schema: {exc.message}") from exc


def _common(run_id: str | None, env: Mapping[str, str] | None) -> dict[str, Any]:
    if run_id is None:
        from core.run_id import run_id as current_run_id

        run_id = current_run_id(env)
    return {
        "id": uuid.uuid4().hex,
        "run_id": run_id,
        "nodeid": None,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def metric_row(nodeid: str | None = None, run_id: str | None = None, **columns: Any) -> dict:
    """One row with the common columns filled in."""
    return {**_common(run_id, None), "nodeid": nodeid, **columns}


def stamp(rows: Sequence[Mapping[str, Any]], env: Mapping[str, str] | None = None) -> list[dict]:
    """Rows with the common columns filled in (caller-set values win)."""
    stamped = []
    for row in rows:
        full = _common(None, env)
        full.update(row)
        stamped.append(full)
    return stamped


class JsonlSink:
    """Append validated rows to ``<root>/<table>.jsonl`` (one O_APPEND write per call)."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root or os.environ.get(METRICS_DIR_ENV) or DEFAULT_DIR)

    def path(self, table: str) -> Path:
        _check_table(table)
        return self.root / f"{table}.jsonl"

    def write(self, table: str, rows: Sequence[Mapping[str, Any]]) -> None:
        path = self.path(table)
        if not rows:
            return
        for row in rows:
            validate_row(table, row)
        path.parent.mkdir(parents=True, exist_ok=True)
        text = "".join(json.dumps(dict(r), sort_keys=True, default=str) + "\n" for r in rows)
        # A single O_APPEND write keeps lines whole across xdist worker processes.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            os.write(fd, text.encode("utf-8"))
        finally:
            os.close(fd)

    def read(self, table: str) -> list[dict[str, Any]]:
        path = self.path(table)
        if not path.is_file():
            return []
        return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]


class MemorySink:
    """Keeps rows in memory (tests, and callers that persist elsewhere). Validates too."""

    def __init__(self) -> None:
        self.rows: dict[str, list[dict[str, Any]]] = {}

    def write(self, table: str, rows: Sequence[Mapping[str, Any]]) -> None:
        if not rows:
            return
        for row in rows:
            validate_row(table, row)
        self.rows.setdefault(table, []).extend(dict(r) for r in rows)


class StoreSink:
    """Adapter onto the results store; needs ``store.write_metrics(table, rows)``."""

    def __init__(self, store) -> None:
        if not callable(getattr(store, "write_metrics", None)):
            raise TypeError("results store does not offer write_metrics(table, rows)")
        self._store = store

    def write(self, table: str, rows: Sequence[Mapping[str, Any]]) -> None:
        _check_table(table)
        if rows:
            self._store.write_metrics(table, [dict(r) for r in rows])


def default_sink(env: Mapping[str, str] | None = None) -> MetricSink | None:
    """``METRICS_SINK``: ``jsonl`` (default), ``store``, or ``off`` (returns None)."""
    env = os.environ if env is None else env
    choice = (env.get(METRICS_SINK_ENV) or "jsonl").strip().lower()
    if choice == "off":
        return None
    if choice == "store":
        from results.store import open_store  # type: ignore[import-not-found]

        return StoreSink(open_store())
    return JsonlSink(env.get(METRICS_DIR_ENV) or None)


def write_metrics(
    table: str,
    rows: Sequence[Mapping[str, Any]],
    sink: MetricSink | None = None,
    env: Mapping[str, str] | None = None,
) -> str | None:
    """Stamp and write rows. Returns None on success, else the reason it failed."""
    try:
        target = sink if sink is not None else default_sink(env)
        if target is None:
            return None
        target.write(table, stamp(rows, env))
        return None
    except Exception as exc:  # a metric write never changes a check outcome
        return f"metrics not written to {table}: {type(exc).__name__}: {exc}"
