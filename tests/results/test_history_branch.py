"""The results-history directory: deterministic bundles, rebuild, prune (no git involved)."""

from __future__ import annotations

from results.cli import main
from results.history import add_bundle_file, add_run, history_bundles, prune_history, rebuild
from results.store import open_store
from tests.results._history import add_run as add_synthetic_run


def test_add_and_rebuild_round_trip(store, tmp_path):
    add_synthetic_run(store, "r1", 1, {"t": "passed"})
    add_synthetic_run(store, "r2", 2, {"t": "failed"})
    hist = tmp_path / "history"
    first = add_run(store, "r1", hist)
    add_run(store, "r2", hist)
    assert first == hist / "bundles" / "2026-01" / "bundle-r1.ndjson.gz"
    assert (hist / "README.md").exists()
    before = first.read_bytes()
    add_run(store, "r1", hist)
    assert first.read_bytes() == before  # identical bytes: a re-add is not a git change

    fresh = open_store(str(tmp_path / "cache.db"))
    report = rebuild(fresh, hist)
    assert sorted(report.runs) == ["r1", "r2"] and not report.failed
    assert rebuild(fresh, hist).failed == []  # idempotent
    assert len(fresh.query("runs_list")) == 2
    fresh.close()


def test_a_corrupt_bundle_is_reported_not_hidden(store, tmp_path):
    add_synthetic_run(store, "r1", 1, {"t": "passed"})
    hist = tmp_path / "history"
    add_run(store, "r1", hist)
    bad = hist / "bundles" / "2026-01" / "bundle-bad.ndjson"
    bad.write_text("{not json\n")
    fresh = open_store(str(tmp_path / "cache.db"))
    report = rebuild(fresh, hist)
    assert report.runs == ["r1"] and len(report.failed) == 1
    fresh.close()
    assert main(["--db", str(tmp_path / "c2.db"), "history", "rebuild", "--dir", str(hist)]) == 1


def test_add_bundle_file_and_prune(store, tmp_path):
    for day in range(1, 4):
        add_synthetic_run(store, f"r{day}", day, {"t": "passed"})
    out = tmp_path / "out"
    hist = tmp_path / "history"
    for day in range(1, 4):
        assert main(["export", "bundle", "--run-id", f"r{day}", "--out", str(out)]) == 0
        add_bundle_file(out / f"bundle-r{day}.ndjson", hist)
    assert len(history_bundles(hist)) == 3
    removed = prune_history(hist, keep_runs=2)
    assert [p.name for p in removed] == ["bundle-r1.ndjson.gz"]
    assert main(["history", "prune", "--dir", str(hist), "--keep-runs", "1"]) == 0
    assert [p.name for p in history_bundles(hist)] == ["bundle-r3.ndjson.gz"]


def test_cli_add_from_store(store, tmp_path, capsys):
    add_synthetic_run(store, "r1", 1, {"t": "passed"})
    hist = tmp_path / "h"
    assert main(["history", "add", "--dir", str(hist), "--run-id", "r1"]) == 0
    assert len(history_bundles(hist)) == 1


def test_two_jobs_sharing_one_run_id_merge_without_loss(tmp_path):
    """One run id, two CI jobs (lanes), two bundles: one run, two invocations, every row."""
    from results.bundle import import_bundle, write_bundle
    from results.models import ResultRecord, RunRecord

    bundles = []
    for lane, start, nodes in (
        ("offline", "2026-01-01T10:00:00.000Z", ["t::a", "t::shared"]),
        ("browser", "2026-01-01T10:01:00.000Z", ["t::b", "t::shared"]),
    ):
        job = open_store(str(tmp_path / f"{lane}.db"))
        job.begin_run(RunRecord(run_id="gh-1-1", lane=lane, started_at=start))
        seq = job.begin_invocation("gh-1-1", [], 1, lane=lane)
        job.conn.execute("UPDATE invocations SET started_at = ?", (start,))
        job.add_results(ResultRecord("gh-1-1", n, "passed", invocation=seq) for n in nodes)
        job.finish_run("gh-1-1", "finished")
        bundles.append(write_bundle(job, "gh-1-1", tmp_path / f"{lane}.ndjson"))
        job.close()

    merged = open_store(str(tmp_path / "merged.db"))
    for b in bundles:
        import_bundle(merged, b)
    for b in bundles:  # re-import: nothing new
        report = import_bundle(merged, b)
        assert report.added.get("result", 0) == 0 and report.added.get("invocation", 0) == 0
    invs = merged.query("run_invocations", run_id="gh-1-1")
    assert [(i["seq"], i["lane"]) for i in invs] == [(1, "offline"), (2, "browser")]
    results = merged.query("run_results", run_id="gh-1-1")
    assert len(results) == 4
    shared = [(r["attempt"], r["invocation_seq"]) for r in results if r["nodeid"] == "t::shared"]
    assert shared == [(1, 1), (2, 2)]
    merged.close()
