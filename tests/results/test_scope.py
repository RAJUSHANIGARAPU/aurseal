"""Removal candidates: a test out of scope in each of its last N runs."""

from __future__ import annotations

from results.cli import main as cli_main
from results.models import ResultRecord, RunRecord, VerdictRecord
from results.scope import removal_candidates, report

ONLY_STAGING = "tests/test_x.py::test_staging_only"
NEVER = "tests/test_x.py::test_never_runs"


def _run(store, n: int, *, staging_ran: bool) -> None:
    run_id = f"run-{n}"
    store.begin_run(RunRecord(run_id=run_id, started_at=f"2026-10-0{n}T10:00:00.000Z"))
    store.add_results(
        [
            ResultRecord(run_id, ONLY_STAGING, "passed" if staging_ran else "skipped"),
            ResultRecord(run_id, NEVER, "skipped"),
        ]
    )
    rows = [VerdictRecord(run_id, NEVER, "coverage", "not_applicable", detail="x")]
    if not staging_ran:
        rows.append(VerdictRecord(run_id, ONLY_STAGING, "coverage", "not_applicable"))
    store.add_verdicts(rows)


def test_only_a_test_out_of_scope_in_every_recent_run_is_a_candidate(store):
    for n in range(1, 5):
        _run(store, n, staging_ran=(n == 2))
    found = removal_candidates(store, runs=3)
    assert [c.nodeid for c in found] == [NEVER]
    assert found[0].last_run == "run-4"
    # The staging-only test ran in run-2; with a window of two runs it qualifies too.
    assert {c.nodeid for c in removal_candidates(store, runs=2)} == {NEVER, ONLY_STAGING}
    # Not enough history is not a verdict.
    assert removal_candidates(store, runs=5) == []
    assert "removal candidate: " + NEVER in report(found, 3)
    assert report([], 3).startswith("no test")


def test_cli_prints_the_report(store, db_path, capsys):
    for n in range(1, 4):
        _run(store, n, staging_ran=True)
    store.close()
    assert cli_main(["--db", str(db_path), "scope", "--runs", "3"]) == 0
    assert "removal candidate: " + NEVER in capsys.readouterr().out
