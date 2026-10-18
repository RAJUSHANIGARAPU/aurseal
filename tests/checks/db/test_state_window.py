"""State windows: a real write between before and after is seen; untouched tables pass."""

from __future__ import annotations

import json

import pytest

from checks.db import DbState, SqliteProbe, diff_rows
from tests.checks.db.conftest import write
from verification.invariants import InvariantSet, changed, non_empty, unchanged

QUERIES = {
    "orders": "SELECT id, status FROM orders ORDER BY id",
    "refunds": "SELECT id, amount FROM refunds ORDER BY id",
}


def test_a_write_is_detected_and_an_untouched_table_passes(db_path):
    state = DbState(SqliteProbe(db_path))
    with state.around(QUERIES) as window:
        write(db_path, "UPDATE orders SET status = 'paid' WHERE id = 1")
    window.assert_invariants(InvariantSet(changed("orders"), unchanged("refunds")))
    diff = window.diff()
    assert diff["refunds"].empty
    assert diff["orders"].changed == [({"id": 1, "status": "open"}, {"id": 1, "status": "paid"})]


def test_invariant_failure_lists_every_violation(db_path):
    state = DbState(SqliteProbe(db_path))
    with state.around(QUERIES) as window:
        write(db_path, "DELETE FROM refunds")
    with pytest.raises(AssertionError) as exc:
        window.assert_invariants(
            InvariantSet(changed("orders"), unchanged("refunds"), non_empty("refunds"))
        )
    message = str(exc.value)
    assert "3 invariant(s) violated" in message
    assert "orders changed" in message and "refunds unchanged" in message


def test_after_is_taken_on_exit_not_later(db_path):
    state = DbState(SqliteProbe(db_path))
    with state.around(QUERIES) as window:
        pass
    write(db_path, "DELETE FROM refunds")  # after the window closed
    assert window.check(InvariantSet(unchanged("refunds"))) == []


def test_rows_without_id_are_diffed_as_multisets():
    diff = diff_rows([{"a": 1}, {"a": 1}], [{"a": 1}, {"a": 2}])
    assert diff.added == [{"a": 2}] and diff.removed == [{"a": 1}]


def test_snapshot_metrics_are_counts_and_digests_not_rows(db_path, tmp_path, monkeypatch):
    monkeypatch.setenv("METRICS_DIR", str(tmp_path / "metrics"))
    state = DbState(SqliteProbe(db_path), record=True)
    with state.around(QUERIES):
        write(db_path, "INSERT INTO refunds (order_id, amount) VALUES (1, 1.0)")
    rows = [
        json.loads(line)
        for line in (tmp_path / "metrics" / "metric_db_snapshot.jsonl").read_text().splitlines()
    ]
    by_phase = {(r["name"], r["phase"]): r for r in rows}
    assert by_phase[("refunds", "before")]["row_count"] == 1
    assert by_phase[("refunds", "after")]["row_count"] == 2
    assert by_phase[("orders", "before")]["digest"] == by_phase[("orders", "after")]["digest"]
    assert "amount" not in json.dumps(rows)
    assert state.problems == []
