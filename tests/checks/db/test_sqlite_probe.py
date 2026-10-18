"""SQLite probe: engine-enforced read-only, snapshot shape, bound parameters, gated writer."""

from __future__ import annotations

import io
import json
import sqlite3

import pytest

from checks.db import SqliteProbe, WriteRefused, WritesNotAllowed, open_probe
from checks.db.__main__ import main
from test_data.cleanup import CleanupLedger


def test_engine_refuses_a_write_even_past_the_guard(db_path):
    probe = SqliteProbe(db_path)
    with pytest.raises(WriteRefused):
        probe.query("INSERT INTO orders (status) VALUES ('x')")
    # Bypass the guard on purpose: the file itself is open read-only.
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        probe._conn.execute("INSERT INTO orders (status) VALUES ('x')")
    assert len(probe.query("SELECT id FROM orders")) == 2


def test_snapshot_shape_and_bound_parameters(db_path):
    probe = open_probe(f"sqlite:///{db_path}")
    snap = probe.snapshot(
        {
            "orders": ("SELECT id, status FROM orders WHERE tag = :tag", {}),
            "refunds": "SELECT order_id, amount FROM refunds",
        },
        {"tag": "run-1"},
    )
    assert snap == {
        "orders": [{"id": 1, "status": "open"}],
        "refunds": [{"order_id": 2, "amount": 5.0}],
    }
    # A hostile value is data, not SQL.
    assert probe.query("SELECT id FROM orders WHERE tag = :t", {"t": "x' OR '1'='1"}) == []


def test_missing_file_and_memory_url_are_refused(tmp_path):
    with pytest.raises(FileNotFoundError):
        SqliteProbe(tmp_path / "absent.sqlite")
    with pytest.raises(ValueError):
        open_probe("sqlite://")


def test_writer_needs_both_switches_and_records_undo(db_path):
    probe = SqliteProbe(db_path)
    with pytest.raises(WritesNotAllowed):
        probe.writer(env={"DB_WRITES_ALLOWED": "true"})
    with pytest.raises(WritesNotAllowed, match="DB_WRITES_ALLOWED"):
        probe.writer(allow_writes=True, env={})
    ledger = CleanupLedger()
    writer = probe.writer(allow_writes=True, ledger=ledger, env={"DB_WRITES_ALLOWED": "true"})
    writer.execute(
        "INSERT INTO orders (id, status, tag) VALUES (:id, 'open', 'fixture')",
        {"id": 99},
        undo=("DELETE FROM orders WHERE id = :id", {"id": 99}),
        ident="order-99",
    )
    assert len(probe.query("SELECT id FROM orders")) == 3
    assert [e.ident for e in ledger.entries] == ["order-99"]
    assert ledger.discard_all().clean
    assert len(probe.query("SELECT id FROM orders")) == 2


def test_cli_snapshot(db_path, tmp_path):
    queries = tmp_path / "q.json"
    queries.write_text(json.dumps({"orders": "SELECT id FROM orders WHERE tag = :tag"}))
    out = io.StringIO()
    args = ["snapshot", "--url", f"sqlite:///{db_path}", "--queries", str(queries)]
    assert main([*args, "--params", '{"tag": "run-2"}'], out) == 0
    assert json.loads(out.getvalue()) == {"orders": [{"id": 2}]}
