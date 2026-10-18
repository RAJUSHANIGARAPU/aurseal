"""SqliteResultsStore: every record type round-trips, natural keys make re-ingest idempotent."""

from __future__ import annotations

import json
import sqlite3

import pytest

from results.models import (
    CaseRecord,
    EvidenceRecord,
    ProvenanceRecord,
    ResultRecord,
    RevisionRecord,
    RunRecord,
    VerdictRecord,
)
from results.store import ResultsStore, StoreError, open_store, row_to_case, row_to_run


def _run(store, run_id="r1", **kw):
    store.begin_run(RunRecord(run_id=run_id, lane="offline", git_branch="main", **kw))
    return store.begin_invocation(run_id, ["tests/"], 2)


def test_the_sqlite_store_satisfies_the_protocol(store):
    assert isinstance(store, ResultsStore)


def test_run_round_trip_keeps_every_column(store):
    run = RunRecord(
        run_id="r1",
        source="api",
        external_id="ext-1",
        name="nightly",
        framework="junit5",
        lane="browser",
        environment="qa",
        sut_version="1.2.3",
        git_sha="abc1234",
        git_branch="main",
        ci_provider="github-actions",
        ci_url="https://ci.example.test/1",
        trigger="schedule",
        schedule_name="nightly",
        run_request_id="rq-1",
        release_label="2026.10",
        expected_count=40,
        host="runner-1",
        provenance={"repository": "example"},
        metadata={"k": "v"},
    )
    store.begin_run(run)
    got = store.run("r1")
    assert got is not None
    for name in ("source", "external_id", "release_label", "expected_count", "schedule_name"):
        assert getattr(got, name) == getattr(run, name)
    assert got.provenance == {"repository": "example"} and got.metadata == {"k": "v"}
    assert got.status == "running" and got.started_at


def test_begin_run_twice_reopens_the_same_run(store):
    _run(store)
    store.finish_run("r1", "finished", "PASS")
    seq = _run(store)
    assert seq == 2
    got = store.run("r1")
    assert got.status == "running" and got.finished_at is None
    assert got.integrity_verdict == "PASS"  # kept until the next verdict replaces it


def test_results_are_idempotent_and_final_results_picks_the_last_attempt(store):
    seq = _run(store)
    rows = [
        ResultRecord("r1", "t.py::a", "failed", 1, invocation=seq, message="boom"),
        ResultRecord("r1", "t.py::a", "passed", 2, invocation=seq),
        ResultRecord("r1", "t.py::b", "skipped", 1, invocation=seq, skip_reason="no env"),
    ]
    assert store.add_results(rows) == 3
    assert store.add_results(rows) == 0
    final = {r["nodeid"]: r for r in store.query("run_final_results", run_id="r1")}
    assert final["t.py::a"]["outcome"] == "passed" and final["t.py::a"]["attempts"] == 2
    assert final["t.py::b"]["outcome"] == "skipped"
    assert store.max_attempts("r1") == {"t.py::a": 2, "t.py::b": 1}


def test_unknown_outcome_is_rejected_by_the_check_constraint(store):
    _run(store)
    with pytest.raises(StoreError):
        store.add_results([ResultRecord("r1", "t::x", "flaky")])


def test_results_for_an_unknown_run_are_refused(store):
    with pytest.raises(StoreError):
        store.add_results([ResultRecord("nope", "t::x", "passed")])


def test_message_and_longrepr_are_truncated(store):
    _run(store)
    store.add_results(
        [ResultRecord("r1", "t::x", "failed", message="m" * 5000, longrepr="l" * 40000)]
    )
    row = store.query("run_results", run_id="r1")[0]
    assert len(row["message"].encode()) <= 2048 and row["message"].endswith("[truncated]")
    assert len(row["longrepr"].encode()) <= 16 * 1024


def test_cases_links_and_revisions_round_trip(store):
    case = CaseRecord(
        case_id="TC-0042",
        case_key="tests/test_x.py::test_a",
        title="A works",
        markers=({"name": "requirement", "args": ["REQ-12"]},),
        links=(("requirement", "REQ-12"), ("feature", "billing")),
    )
    store.upsert_cases([case])
    store.upsert_cases([case])
    assert store.add_revisions([RevisionRecord("rev1", "TC-0042", steps=("open", "check"))]) == 1
    assert store.add_revisions([RevisionRecord("rev1", "TC-0042")]) == 0
    links = store.query("case_links_for", case_id="TC-0042")
    assert {(r["kind"], r["ref"]) for r in links} == {
        ("requirement", "REQ-12"),
        ("feature", "billing"),
    }
    row = store.conn.execute("SELECT * FROM cases WHERE case_id='TC-0042'").fetchone()
    got = row_to_case(dict(row))
    assert got.current_revision_id == "rev1" and got.markers[0]["args"] == ["REQ-12"]


def test_a_pinned_case_moving_onto_a_held_key_supersedes_the_old_row(store):
    store.upsert_cases([CaseRecord("c-old", "t.py::a")])
    store.upsert_cases([CaseRecord("TC-1", "t.py::a")])
    rows = {r[0]: r[1] for r in store.conn.execute("SELECT case_id, status FROM cases")}
    assert rows == {"c-old": "removed", "TC-1": "active"}


def test_evidence_links_resolve_to_the_final_attempt(store):
    seq = _run(store)
    store.add_results(
        [
            ResultRecord("r1", "t::a", "failed", 1, invocation=seq),
            ResultRecord("r1", "t::a", "passed", 2, invocation=seq),
        ]
    )
    ev = [
        EvidenceRecord("r1", "trace", "traces/a.zip", nodeid="t::a"),
        EvidenceRecord("r1", "allure", "reports/allure-results"),
    ]
    assert store.add_evidence(ev) == 2
    assert store.add_evidence(ev) == 0
    rows = store.query("run_evidence", run_id="r1")
    by_kind = {r["kind"]: r for r in rows}
    final_id = store.query("run_final_results", run_id="r1")[0]["id"]
    assert by_kind["trace"]["result_id"] == final_id and by_kind["trace"]["attempt"] == 2
    assert by_kind["allure"]["result_id"] is None and by_kind["allure"]["nodeid"] == ""


def test_verdicts_ingest_twice_into_one_set_of_rows(store, tmp_path):
    _run(store)
    path = tmp_path / "v.jsonl"
    lines = [
        {
            "run_id": "r1",
            "nodeid": "t::a",
            "kind": "expected_red",
            "ticket": "BUG-142",
            "verdict": "reproduces",
            "detail": "x",
            "subject": "BUG-142",
            "timestamp": "2026-01-01T00:00:00+00:00",
        },
        {
            "run_id": "r1",
            "nodeid": "t::b",
            "kind": "coverage",
            "ticket": None,
            "verdict": "proved",
            "detail": "",
            "subject": "REQ-12",
            "timestamp": "2026-01-01T00:00:01+00:00",
        },
        {
            "run_id": "other",
            "nodeid": "t::c",
            "kind": "coverage",
            "ticket": None,
            "verdict": "proved",
            "detail": "",
            "subject": None,
            "timestamp": "x",
        },
    ]
    path.write_text("".join(json.dumps(x) + "\n" for x in lines) + "not json\n")
    assert store.ingest_verdicts(path, "r1") == 2
    assert store.ingest_verdicts(path, "r1") == 0
    rows = store.query("run_verdicts", run_id="r1")
    assert {r["verdict"] for r in rows} == {"reproduces", "proved"}


def test_data_origin_ingest_is_idempotent(store, tmp_path):
    _run(store)
    path = tmp_path / "u.jsonl"
    path.write_text(
        json.dumps(
            {
                "run_id": "r1",
                "nodeid": "t::a",
                "origin": "fresh",
                "fixture": "order",
                "entity_id": "ORD-42",
                "timestamp": "2026-01-01T00:00:00+00:00",
            }
        )
        + "\n"
    )
    assert store.ingest_provenance(path, "r1") == 1
    assert store.ingest_provenance(path, "r1") == 0
    assert store.add_provenance([ProvenanceRecord("r1", "t::b", "reused")]) == 1


def test_verdict_record_maps_timestamp_to_recorded_at():
    v = VerdictRecord.from_row(
        {"nodeid": "n", "kind": "coverage", "verdict": "proved", "timestamp": "T"}, "r1"
    )
    assert v.recorded_at == "T" and v.run_id == "r1"


def test_query_rejects_unknown_names_and_parameters(store):
    with pytest.raises(StoreError, match="unknown query"):
        store.query("drop_everything")
    with pytest.raises(StoreError, match="invalid query name"):
        store.query("../etc/passwd")
    with pytest.raises(StoreError, match="takes no parameter"):
        store.query("run_get", run_id="x", sql="1")


def test_readonly_store_reads_but_cannot_write(db_path):
    w = open_store(str(db_path))
    _run(w)
    w.close()
    r = open_store(str(db_path), readonly=True)
    assert r.run("r1") is not None
    with pytest.raises(sqlite3.OperationalError):
        r.begin_run(RunRecord(run_id="r2"))
    r.close()


def test_readonly_on_a_missing_file_is_an_error(tmp_path):
    with pytest.raises(StoreError):
        open_store(str(tmp_path / "missing.db"), readonly=True)


def test_store_urls(tmp_path):
    s = open_store(f"sqlite:///{tmp_path / 'u.db'}")
    s.close()
    assert (tmp_path / "u.db").exists()
    with pytest.raises(StoreError, match="only SQLite"):
        open_store("postgresql://example.test/db")


def test_default_path_comes_from_results_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("RESULTS_DIR", str(tmp_path / "rd"))
    s = open_store()
    s.close()
    assert (tmp_path / "rd" / "results.db").exists()


def test_wal_and_foreign_keys_are_on(store):
    assert store.conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert store.conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_prune_keeps_the_newest_runs(store):
    for i in range(5):
        store.begin_run(RunRecord(run_id=f"r{i}", started_at=f"2026-01-0{i + 1}T00:00:00.000Z"))
        store.add_results([ResultRecord(f"r{i}", "t::a", "passed")])
    assert store.prune(keep_runs=2) == 3
    left = [r["run_id"] for r in store.query("runs_list")]
    assert left == ["r4", "r3"]
    # Cascades: no orphan results.
    assert store.conn.execute("SELECT COUNT(*) FROM test_results").fetchone()[0] == 2
    assert store.prune(older_than="2026-01-05T00:00:00.000Z") == 1


def test_runs_list_filters(store):
    store.begin_run(RunRecord(run_id="a", lane="offline", git_branch="main"))
    store.begin_run(RunRecord(run_id="b", lane="browser", git_branch="main"))
    assert [r["run_id"] for r in store.query("runs_list", lane="browser")] == ["b"]
    assert row_to_run(store.query("run_get", run_id="a")[0]).lane == "offline"


def test_projects_are_created_on_demand(store):
    store.begin_run(RunRecord(run_id="p1", project_id="team-b"))
    assert store.conn.execute("SELECT project_key FROM projects WHERE id='team-b'").fetchone()
