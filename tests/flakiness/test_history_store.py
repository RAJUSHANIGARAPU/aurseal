"""Tests for autonomous_ui.flakiness.history_store."""

from __future__ import annotations

from pathlib import Path

import pytest

from autonomous_ui.flakiness.history_store import HistoryStore
from autonomous_ui.flakiness.models import FlakRecord


def _record(
    test_id: str = "test_example",
    outcome: str = "passed",
    error: str = "",
    worker: str = "main",
) -> FlakRecord:
    return FlakRecord(
        test_id=test_id,
        run_id="20260425T000000Z",
        outcome=outcome,
        duration_s=1.0,
        error=error,
        timestamp="2026-04-25T00:00:00Z",
        worker=worker,
        environment="qa",
    )


@pytest.fixture()
def store(tmp_path: Path) -> HistoryStore:
    return HistoryStore(store_path=tmp_path / "history.jsonl")


# ------------------------------------------------------------------
# record() and load_all()
# ------------------------------------------------------------------


def test_record_creates_file(store: HistoryStore, tmp_path: Path) -> None:
    store.record(_record())
    assert (tmp_path / "history.jsonl").exists()


def test_record_and_load_round_trip(store: HistoryStore) -> None:
    rec = _record(test_id="test_login", outcome="failed", error="AssertionError: expected x")
    store.record(rec)
    loaded = store.load_all()
    assert len(loaded) == 1
    assert loaded[0].test_id == "test_login"
    assert loaded[0].outcome == "failed"
    assert loaded[0].error == "AssertionError: expected x"


def test_multiple_records_preserve_order(store: HistoryStore) -> None:
    for i in range(5):
        store.record(_record(test_id=f"test_{i}"))
    loaded = store.load_all()
    assert [r.test_id for r in loaded] == [f"test_{i}" for i in range(5)]


def test_load_all_empty_when_no_file(tmp_path: Path) -> None:
    store = HistoryStore(store_path=tmp_path / "nonexistent.jsonl")
    assert store.load_all() == []


def test_load_all_skips_corrupt_lines(store: HistoryStore, tmp_path: Path) -> None:
    store.record(_record(test_id="test_good"))
    jsonl_path = tmp_path / "history.jsonl"
    with open(jsonl_path, "a") as fh:
        fh.write("NOT JSON AT ALL\n")
    store.record(_record(test_id="test_also_good"))
    loaded = store.load_all()
    ids = [r.test_id for r in loaded]
    assert "test_good" in ids
    assert "test_also_good" in ids
    assert len(ids) == 2  # corrupt line skipped


def test_load_all_skips_blank_lines(store: HistoryStore, tmp_path: Path) -> None:
    store.record(_record())
    jsonl_path = tmp_path / "history.jsonl"
    with open(jsonl_path, "a") as fh:
        fh.write("\n\n")
    store.record(_record())
    assert len(store.load_all()) == 2


# ------------------------------------------------------------------
# load_for_test()
# ------------------------------------------------------------------


def test_load_for_test_filters_by_id(store: HistoryStore) -> None:
    store.record(_record(test_id="test_a"))
    store.record(_record(test_id="test_b"))
    store.record(_record(test_id="test_a"))
    result = store.load_for_test("test_a")
    assert all(r.test_id == "test_a" for r in result)
    assert len(result) == 2


def test_load_for_test_respects_limit(store: HistoryStore) -> None:
    for _ in range(10):
        store.record(_record())
    assert len(store.load_for_test("test_example", limit=3)) == 3


def test_load_for_test_returns_last_n(store: HistoryStore) -> None:
    for i in range(10):
        store.record(_record(test_id="test_x", outcome="passed" if i < 8 else "failed"))
    records = store.load_for_test("test_x", limit=2)  # records 8 and 9 are failed
    assert all(r.outcome == "failed" for r in records)


# ------------------------------------------------------------------
# grouped_by_test()
# ------------------------------------------------------------------


def test_grouped_by_test_groups_correctly(store: HistoryStore) -> None:
    store.record(_record(test_id="test_a"))
    store.record(_record(test_id="test_b"))
    store.record(_record(test_id="test_a"))
    groups = store.grouped_by_test()
    assert set(groups.keys()) == {"test_a", "test_b"}
    assert len(groups["test_a"]) == 2
    assert len(groups["test_b"]) == 1


def test_grouped_by_test_returns_empty_dict_when_no_data(store: HistoryStore) -> None:
    assert store.grouped_by_test() == {}


# ------------------------------------------------------------------
# FlakRecord.from_dict round-trip
# ------------------------------------------------------------------


def test_flak_record_from_dict_handles_missing_fields() -> None:
    rec = FlakRecord.from_dict({})
    assert rec.test_id == ""
    assert rec.outcome == "unknown"
    assert rec.duration_s == 0.0
    assert rec.worker == "main"
