"""The verdict record: the file contract the run-trust gate reads."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from verification.verdicts import (
    CoverageOutcome,
    Kind,
    RedVerdict,
    current_nodeid,
    file_size,
    read_verdicts,
    record_verdict,
    suppress_recording,
)

ROOT = Path(__file__).resolve().parents[2]
CONTRACT_KEYS = {"run_id", "nodeid", "kind", "ticket", "verdict", "detail"}


def test_record_carries_every_contract_field(isolated_verdicts, monkeypatch):
    monkeypatch.setenv("TEST_RUN_ID", "run-7")
    record_verdict(Kind.EXPECTED_RED, RedVerdict.REPRODUCES, ticket="BUG-1", detail="  a\n b ")
    [line] = isolated_verdicts.read_text().splitlines()
    entry = json.loads(line)
    assert CONTRACT_KEYS <= entry.keys()
    assert entry["run_id"] == "run-7"
    assert entry["kind"] == "expected_red" and entry["verdict"] == "reproduces"
    assert entry["ticket"] == "BUG-1" and entry["subject"] == "BUG-1"
    assert entry["detail"] == "a b"
    assert entry["nodeid"].endswith("test_record_carries_every_contract_field")


def test_coverage_record_without_ticket_has_null_ticket(isolated_verdicts):
    record_verdict(Kind.COVERAGE, CoverageOutcome.PROVED, subject="REQ-3", detail="x=1")
    [entry] = read_verdicts()
    assert entry["ticket"] is None and entry["subject"] == "REQ-3"
    assert entry["kind"] == "coverage" and entry["verdict"] == "proved"


def test_unknown_verdict_for_kind_is_a_caller_bug():
    with pytest.raises(ValueError):
        record_verdict(Kind.COVERAGE, "reproduces")
    with pytest.raises(ValueError):
        record_verdict("expected_red", "proved")
    with pytest.raises(ValueError):
        record_verdict("other", "proved")


def test_detail_is_flattened_and_capped():
    entry = record_verdict(Kind.COVERAGE, "recorded", subject="S", detail="x" * 900)
    assert entry is not None and len(entry["detail"]) == 501


def test_read_filters_by_run_id_and_offset(monkeypatch):
    monkeypatch.setenv("TEST_RUN_ID", "a")
    record_verdict(Kind.EXPECTED_RED, "cleared", ticket="BUG-1")
    offset = file_size()
    monkeypatch.setenv("TEST_RUN_ID", "b")
    record_verdict(Kind.EXPECTED_RED, "reproduces", ticket="BUG-2")
    assert [r["ticket"] for r in read_verdicts(run_id="a")] == ["BUG-1"]
    assert [r["ticket"] for r in read_verdicts(start=offset)] == ["BUG-2"]
    assert len(read_verdicts()) == 2


def test_missing_file_reads_as_empty(tmp_path):
    assert read_verdicts(tmp_path / "nope.jsonl") == []
    assert file_size(tmp_path / "nope.jsonl") == 0


def test_malformed_lines_are_skipped_not_fatal(isolated_verdicts):
    record_verdict(Kind.EXPECTED_RED, "reproduces", ticket="BUG-1")
    with isolated_verdicts.open("a") as handle:
        handle.write('{"truncated": \n[1, 2]\n\n')
    record_verdict(Kind.EXPECTED_RED, "cleared", ticket="BUG-2")
    assert [r["ticket"] for r in read_verdicts()] == ["BUG-1", "BUG-2"]


def test_suppressed_recording_writes_nothing(isolated_verdicts):
    with suppress_recording():
        assert record_verdict(Kind.EXPECTED_RED, "cleared", ticket="BUG-1") is None
    assert not isolated_verdicts.exists()


def test_unwritable_target_never_raises(tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("")
    monkeypatch.setenv("VERDICTS_FILE", str(blocker / "sub" / "v.jsonl"))
    assert record_verdict(Kind.EXPECTED_RED, "cleared", ticket="BUG-1") is None


def test_current_nodeid_strips_the_phase(monkeypatch):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/x.py::test_a[1] (call)")
    assert current_nodeid() == "tests/x.py::test_a[1]"
    monkeypatch.delenv("PYTEST_CURRENT_TEST")
    assert current_nodeid() == ""


def test_parallel_writers_never_interleave_lines(isolated_verdicts):
    """Several processes appending at once (what xdist workers do) leave whole lines only."""
    script = textwrap.dedent(
        """
        import sys
        from verification.verdicts import record_verdict
        for i in range(150):
            record_verdict("coverage", "recorded", subject=f"{sys.argv[1]}-{i}", detail="y" * 400)
        """
    )
    env = {**os.environ, "PYTHONPATH": str(ROOT), "VERDICTS_FILE": str(isolated_verdicts)}
    procs = [
        subprocess.Popen([sys.executable, "-c", script, f"w{n}"], env=env, cwd=ROOT)
        for n in range(4)
    ]
    assert all(p.wait(timeout=60) == 0 for p in procs)
    lines = isolated_verdicts.read_text().splitlines()
    assert len(lines) == 600
    assert len({json.loads(line)["subject"] for line in lines}) == 600
