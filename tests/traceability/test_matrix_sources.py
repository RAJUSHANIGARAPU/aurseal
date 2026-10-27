"""The artefact source and the store source give the same matrix for the same data."""

from __future__ import annotations

import pytest

from results.api import ApiError, dispatch
from results.models import RevisionRecord
from results.store import open_store
from tests.traceability._runs import (
    RunSpec,
    T,
    coverage,
    load_store,
    red,
    registry,
    write_artifacts,
)
from traceability import api  # noqa: F401 - registers the routes
from traceability.evidence import ArtifactResultSource, Scope, StoreResultSource
from traceability.matrix import build_matrix
from traceability.store import load_matrix_snapshot, save_matrix, save_registry

M = "tests/test_m.py::"
RUNS = [
    RunSpec(
        "r1",
        [
            T(M + "a", reqs=("REQ-1",)),
            T(M + "b", "failed", reqs=("REQ-2",)),
            T(M + "c", reqs=("REQ-3",)),
            T(M + "d", reqs=("REQ-4",), draft=True),
            T(M + "e", "skipped", reqs=("REQ-5",)),
        ],
        verdicts=[coverage("r1", M + "a", "REQ-1 x"), red("r1", M + "c", "BUG-3")],
    ),
    RunSpec(
        "r2", [T(M + "f", reqs=("REQ-6",))], integrity="FAIL", timestamp="2026-01-02T00:00:00+00:00"
    ),
]
IDS = [f"REQ-{i}" for i in range(1, 8)]


@pytest.fixture
def store(tmp_path):
    s = open_store(str(tmp_path / "r.db"))
    load_store(s, RUNS)
    yield s
    s.close()


def _shape(m):
    return [
        (
            r.requirement_id,
            r.status.value,
            [(c.nodeid, c.outcome, c.state, c.coverage_verdict, c.draft) for c in r.cells],
        )
        for r in m.rows
    ]


def test_artifact_and_store_sources_agree(tmp_path, store):
    write_artifacts(tmp_path / "reports", RUNS)
    from_files = build_matrix(registry(*IDS), None, ArtifactResultSource(tmp_path / "reports"))
    from_db = build_matrix(registry(*IDS), None, StoreResultSource(store, Scope(("r1", "r2"))))
    assert _shape(from_files) == _shape(from_db)
    assert {r.status.value for r in from_db.rows} >= {"PROVED", "FAILING", "KNOWN_DEFECT"}


def test_store_scopes(store):
    by_branch = StoreResultSource(store, Scope(branch="main", last=1))
    assert [r.run_id for r in by_branch.runs()] == ["r2"]
    with pytest.raises(ValueError, match="unscoped"):
        StoreResultSource(store, Scope())


def test_stale_is_flagged_from_store_revisions(store):
    from results.catalogue import case_id_for_nodeid

    case = case_id_for_nodeid(M + "a")
    store.add_revisions([RevisionRecord("rev-old", case), RevisionRecord("rev-new", case)])
    store.conn.execute(
        "UPDATE test_results SET revision_id = 'rev-old' WHERE nodeid = ?", (M + "a",)
    )
    store.conn.execute(
        "UPDATE cases SET current_revision_id = 'rev-new' WHERE case_id = ?", (case,)
    )
    m = build_matrix(registry(*IDS), None, StoreResultSource(store, Scope(("r1",))))
    assert m.row("REQ-1").stale is True and m.row("REQ-2").stale is None


def test_snapshot_round_trip(store):
    m = build_matrix(registry(*IDS), None, StoreResultSource(store, Scope(("r1",))))
    sid = save_matrix(store, m.to_json())
    assert load_matrix_snapshot(store, sid)["run_ids"] == ["r1"]


def test_rest_matrix_needs_a_scope(store):
    save_registry(store, registry(*IDS))
    with pytest.raises(ApiError) as err:
        dispatch(store, "GET", "/traceability/matrix")
    assert err.value.status == 400
    doc = dispatch(store, "GET", "/traceability/matrix", params={"runs": "r1"})
    assert doc["counts"]["PROVED"] == 1
    md = dispatch(store, "GET", "/traceability/matrix", params={"runs": "r1", "format": "md"})
    assert md["body"].startswith("## Requirements traceability")
    row = dispatch(store, "GET", "/traceability/requirements/REQ-2", params={"runs": "r1"})
    assert row["status"] == "FAILING" and row["requirement"]["title"] == "title REQ-2"
    with pytest.raises(ApiError):
        dispatch(store, "GET", "/traceability/requirements/REQ-404", params={"runs": "r1"})


def test_both_sources_see_a_flaky_pass(tmp_path):
    runs = [RunSpec("r1", [T(M + "a", reqs=("REQ-1",), failed_first="Timeout 5000ms exceeded")])]
    write_artifacts(tmp_path / "reports", runs)
    s = open_store(str(tmp_path / "f.db"))
    try:
        load_store(s, runs)
        from_db = build_matrix(registry("REQ-1"), None, StoreResultSource(s, Scope(("r1",))))
    finally:
        s.close()
    from_files = build_matrix(registry("REQ-1"), None, ArtifactResultSource(tmp_path / "reports"))
    for m in (from_files, from_db):
        cell = m.row("REQ-1").cells[0]
        assert (m.row("REQ-1").status.value, cell.state) == ("FLAKY", "flaky")
        assert cell.first_failure == "Timeout 5000ms exceeded"
