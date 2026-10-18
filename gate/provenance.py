"""Fixture provenance: one JSONL row per piece of test data handed to a test.

A test can come by its data two ways that are not equally trustworthy. Data the
run *built* from a fixture it owns has a known shape. Data *picked* from
whatever a shared environment happens to hold has only passed the picker's
filter; nothing checks it resembles what the assertions assume. A green on the
second is a weaker claim than a green on the first.

This module records which of the two happened, per test, so the run gate can
ask "did anything that passed actually stand on data it acquired?" (rule 4).
It is measurement only: every function swallows its own failures, because
instrumentation that can fail a test turns a reporting problem into a
product-looking red.

Attribution. Rows are attributed to the test running in this process, read
from ``PYTEST_CURRENT_TEST`` — which pytest sets per process, so it is correct
on every xdist worker without thread-local bookkeeping. Data acquired by a
module- or class-scoped fixture belongs to that scope, not to whichever test
happened to trigger it: pass ``nodeid=request.node.nodeid`` and the row covers
every test underneath it.

Rows from all workers are appended to one file (one short ``write`` per row in
append mode) and carry the run id, so a reader can keep only this run's rows.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from core.logger import logger
from gate.run_id import current_run_id

ENV = "DATA_ORIGIN_FILE"
DEFAULT_PATH = Path("reports") / "data-origin.jsonl"
NO_TEST = "(outside a test)"


class Origin(str, Enum):
    """How a test came by its data."""

    FRESH = "fresh"  # built by this run from an owned fixture: shape known by construction
    REUSED = "reused"  # picked from the environment's pool: shape unverified


@dataclass(frozen=True)
class FixtureRow:
    run_id: str | None
    nodeid: str
    origin: str
    fixture: str
    entity_id: str | None = None
    candidates: int = 1
    attributes: dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""

    def covers(self, test_nodeid: str) -> bool:
        """Whether this row stands behind ``test_nodeid`` (same test, or an enclosing scope)."""
        return test_nodeid == self.nodeid or test_nodeid.startswith(self.nodeid + "::")


def usage_path(path: str | Path | None = None) -> Path:
    if path is not None:
        return Path(path)
    return Path(os.environ.get(ENV) or DEFAULT_PATH)


def current_nodeid() -> str:
    """The node id of the test running in this process, from ``PYTEST_CURRENT_TEST``."""
    raw = os.environ.get("PYTEST_CURRENT_TEST", "").strip()
    if not raw:
        return NO_TEST
    # "tests/x.py::test_y[a b] (call)" — the phase suffix is always the last token.
    head, sep, tail = raw.rpartition(" ")
    return head if sep and tail.startswith("(") and tail.endswith(")") else raw


def record_fixture(
    origin: Origin | str,
    fixture: str,
    *,
    entity_id: str | None = None,
    nodeid: str | None = None,
    candidates: int = 1,
    attributes: dict[str, Any] | None = None,
    path: str | Path | None = None,
) -> bool:
    """Append one row. Returns False (and logs) on any problem; never raises.

    ``attributes`` is best-effort shape information the caller already holds.
    A missing attribute means "not observed", never "absent from the data".
    ``candidates`` is the shortlist size when a picker offers several items
    and cannot know which one the test will use.
    """
    try:
        row = FixtureRow(
            run_id=current_run_id(),
            nodeid=nodeid or current_nodeid(),
            origin=Origin(origin).value,
            fixture=str(fixture),
            entity_id=entity_id,
            candidates=int(candidates),
            attributes=dict(attributes or {}),
            timestamp=datetime.now(tz=timezone.utc).isoformat(),
        )
        target = usage_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(asdict(row), default=str) + "\n"
        with target.open("a", encoding="utf-8") as handle:
            handle.write(line)
        return True
    except Exception as exc:
        logger.warning("[data-origin] could not record %s (%s): %s", fixture, entity_id, exc)
        return False


def read_rows(path: str | Path | None = None, run_id: str | None = None) -> list[FixtureRow]:
    """This run's rows (all rows when ``run_id`` is None); an unreadable file reads as empty."""
    target = usage_path(path)
    rows: list[FixtureRow] = []
    try:
        if not target.is_file():
            return rows
        lines = target.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        logger.warning("[data-origin] could not read %s: %s", target, exc)
        return rows
    for line in lines:
        if not line.strip():
            continue
        try:
            data = json.loads(line)
            row = FixtureRow(
                run_id=data.get("run_id"),
                nodeid=str(data["nodeid"]),
                origin=str(data["origin"]),
                fixture=str(data.get("fixture", "")),
                entity_id=data.get("entity_id"),
                candidates=int(data.get("candidates", 1)),
                attributes=dict(data.get("attributes") or {}),
                timestamp=str(data.get("timestamp", "")),
            )
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
        if run_id is not None and row.run_id != run_id:
            continue
        rows.append(row)
    return rows
