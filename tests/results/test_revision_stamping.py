"""Two runs with an edited test produce two revisions; each row points at its own."""

from __future__ import annotations

from results.api import dispatch
from results.revision import revisions_for_case
from tests.results._helpers import rows
from tests.results.conftest import PLUGIN

pytest_plugins = ["pytester"]


def test_an_edited_test_gets_a_new_revision(pytester, db_path, monkeypatch, store):
    monkeypatch.setenv("TEST_RUN_ID", "run-1")
    pytester.makepyfile(test_rev="def test_a():\n    assert 1\n\ndef test_b():\n    pass\n")
    pytester.runpytest(*PLUGIN)
    monkeypatch.setenv("TEST_RUN_ID", "run-2")
    pytester.makepyfile(test_rev="def test_a():\n    assert 2\n\ndef test_b():\n    pass\n")
    pytester.runpytest(*PLUGIN)

    stamped = rows(
        db_path,
        "SELECT run_id, nodeid, case_id, revision_id FROM test_results ORDER BY run_id, nodeid",
    )
    a = [r for r in stamped if r["nodeid"].endswith("test_a")]
    b = [r for r in stamped if r["nodeid"].endswith("test_b")]
    assert a[0]["revision_id"] != a[1]["revision_id"]
    assert b[0]["revision_id"] == b[1]["revision_id"]  # untouched function, same revision

    revs = revisions_for_case(store, a[0]["case_id"])
    assert [r.first_seen_run_id for r in revs] == ["run-1", "run-2"]
    assert all(r.file_blob_sha and r.file_blob_sha != r.revision_id for r in revs)
    assert len({r.file_blob_sha for r in revs}) == 2  # the file changed as well
    case = rows(db_path, "SELECT current_revision_id FROM cases WHERE case_id = ?", a[0]["case_id"])
    assert case[0]["current_revision_id"] == a[1]["revision_id"]

    ranges = dispatch(store, "GET", f"/cases/{a[0]['case_id']}/revisions")["revisions"]
    assert [(r["first_run_id"], r["last_run_id"], r["run_count"]) for r in ranges] == [
        ("run-1", "run-1", 1),
        ("run-2", "run-2", 1),
    ]
    b_ranges = dispatch(store, "GET", f"/cases/{b[0]['case_id']}/revisions")["revisions"]
    assert [(r["first_run_id"], r["last_run_id"], r["run_count"]) for r in b_ranges] == [
        ("run-1", "run-2", 2)
    ]


def test_cli_lists_revisions(pytester, db_path, capsys, monkeypatch):
    from results.cli import main

    pytester.makepyfile(test_rev="def test_a():\n    pass\n")
    pytester.runpytest(*PLUGIN)
    case_id = rows(db_path, "SELECT case_id FROM cases")[0]["case_id"]
    capsys.readouterr()
    assert main(["case", "revisions", case_id]) == 0
    assert "runs 1" in capsys.readouterr().out
    assert main(["case", "revisions", "missing"]) == 2
