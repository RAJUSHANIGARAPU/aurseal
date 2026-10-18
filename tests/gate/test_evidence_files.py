"""Run id, verdict rows and fixture provenance: the evidence files the gate reads."""

from __future__ import annotations

import json
from types import SimpleNamespace

from gate import run_id
from gate.provenance import NO_TEST, FixtureRow, current_nodeid, read_rows, record_fixture
from gate.verdicts import read_verdict_rows, verdicts_path


def test_run_id_is_generated_once_and_exported(monkeypatch):
    monkeypatch.delenv("TEST_RUN_ID")
    first = run_id.ensure_run_id()
    assert first and run_id.current_run_id() == first
    assert run_id.ensure_run_id() == first


def test_worker_takes_the_controllers_run_id(monkeypatch):
    worker = SimpleNamespace(workerinput={"test_run_id": "from-controller", "workerid": "gw0"})
    assert run_id.ensure_run_id(worker) == "from-controller"
    assert run_id.current_run_id() == "from-controller"


def test_verdict_reader_is_tolerant(tmp_path, monkeypatch):
    path = tmp_path / "v.jsonl"
    lines = [
        {"run_id": "r1", "nodeid": "a", "kind": "expected_red", "ticket": "BUG-1",
         "verdict": "reproduces", "detail": "x"},
        {"run_id": "r2", "nodeid": "b", "kind": "coverage", "verdict": "proved"},
        {"run_id": "r1", "nodeid": "c", "kind": "expected_red"},
        ["not", "an", "object"],
    ]  # fmt: skip
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n{broken\n\n")
    rows = read_verdict_rows(path)
    assert [(r.nodeid, r.key) for r in rows] == [("a", "BUG-1"), ("b", "b")]
    assert [r.nodeid for r in read_verdict_rows(path, run_id="r1")] == ["a"]
    assert read_verdict_rows(tmp_path / "absent.jsonl") == []
    monkeypatch.setenv("VERDICTS_FILE", str(path))
    assert verdicts_path() == path


def test_current_nodeid(monkeypatch):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/x.py::test_y[a b] (call)")
    assert current_nodeid() == "tests/x.py::test_y[a b]"
    monkeypatch.delenv("PYTEST_CURRENT_TEST")
    assert current_nodeid() == NO_TEST


def test_record_and_read_back(tmp_path, monkeypatch):
    path = tmp_path / "usage.jsonl"
    monkeypatch.setenv("TEST_RUN_ID", "r1")
    assert record_fixture(
        "fresh", "orders/basic.json", entity_id="E-1", attributes={"items": 2}, path=path
    )
    assert record_fixture("reused", "pool", nodeid="tests/m.py::T", candidates=3, path=path)
    monkeypatch.setenv("TEST_RUN_ID", "r2")
    record_fixture("fresh", "other", path=path)
    with path.open("a") as handle:
        handle.write("{broken\n")
    rows = read_rows(path, run_id="r1")
    assert [r.origin for r in rows] == ["fresh", "reused"]
    assert rows[0].attributes == {"items": 2}
    assert rows[1].covers("tests/m.py::T::test_a") and not rows[1].covers("tests/m.py::Tx")
    assert len(read_rows(path)) == 3


def test_recording_never_raises(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("")
    assert record_fixture("fresh", "x", path=blocker / "usage.jsonl") is False
    assert record_fixture("not-an-origin", "x", path=tmp_path / "u.jsonl") is False
    assert read_rows(tmp_path / "absent.jsonl") == []


def test_row_covers_itself():
    assert FixtureRow(None, "a.py::test_x", "fresh", "f").covers("a.py::test_x")
