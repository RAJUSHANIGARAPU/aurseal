"""pytest plugin for collect-only subprocesses: dump every item's trace attributes, run nothing.

Loaded with ``-p traceability._collector`` under ``--collect-only``; writes JSON to
``$TRACE_COLLECT_OUT``: ``{"items": [{nodeid, markers, requirements, invalid, ...}],
"errors": [...]}``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from traceability.pytest_plugin import register

OUT_ENV = "TRACE_COLLECT_OUT"
_state: dict[str, list] = {"items": [], "errors": []}


def pytest_configure(config: pytest.Config) -> None:
    register(config)


def pytest_collectreport(report: pytest.CollectReport) -> None:
    if report.failed:
        _state["errors"].append(f"{report.nodeid or '(session)'}: collection error")


def pytest_collection_finish(session: pytest.Session) -> None:
    plugin = register(session.config)
    items = []
    for item in session.items:
        entry = plugin.item_entry(item)
        entry["nodeid"] = item.nodeid
        entry["markers"] = sorted({m.name for m in item.iter_markers()})
        entry.setdefault("requirements", [])
        entry.setdefault("invalid", [])
        items.append(entry)
    _state["items"] = items


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    target = os.environ.get(OUT_ENV)
    if target:
        Path(target).write_text(json.dumps(_state), encoding="utf-8")
