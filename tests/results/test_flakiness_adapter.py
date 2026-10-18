"""Store history fed to the flakiness detector classifies like the equivalent JSONL."""

from __future__ import annotations

from autonomous_ui.flakiness.detector import FlakinessDetector
from autonomous_ui.flakiness.history_store import HistoryStore
from autonomous_ui.flakiness.models import FlakRecord
from results.adapters.flakiness import grouped_history, history_from_store
from tests.results._history import add_run

NODE = "tests/test_x.py::{}"


def _jsonl_equivalent(tmp_path) -> HistoryStore:
    hs = HistoryStore(tmp_path / "flaky.jsonl")
    plan = [
        ("r1", "flaky", ["rerun", "passed"]),
        ("r1", "solid", ["passed"]),
        ("r2", "flaky", ["passed"]),
        ("r2", "solid", ["passed"]),
        ("r3", "flaky", ["rerun", "passed"]),
        ("r3", "solid", ["passed"]),
        ("r4", "flaky", ["failed"]),
        ("r4", "solid", ["passed"]),
        ("r5", "flaky", ["passed"]),
        ("r5", "solid", ["passed"]),
    ]
    for run, name, outcomes in plan:
        for out in outcomes:
            hs.record(FlakRecord(NODE.format(name), run, out, 0.1, "", "", "main", "qa"))
    return hs


def test_same_classification_as_jsonl(store, tmp_path):
    add_run(store, "r1", 1, {"flaky": ["failed", "passed"], "solid": "passed"})
    add_run(store, "r2", 2, {"flaky": "passed", "solid": "passed"})
    add_run(store, "r3", 3, {"flaky": ["failed", "passed"], "solid": "passed"})
    add_run(store, "r4", 4, {"flaky": "failed", "solid": "passed"})
    add_run(store, "r5", 5, {"flaky": "passed", "solid": "passed"})

    jsonl = _jsonl_equivalent(tmp_path)
    detector = FlakinessDetector(jsonl)
    expected = {
        p.test_id: (p.is_flaky, p.total_runs, p.failure_count, p.flaky_pass_count)
        for p in detector.get_profiles()
    }
    from_store = {
        p.test_id: (p.is_flaky, p.total_runs, p.failure_count, p.flaky_pass_count)
        for p in detector.get_profiles(groups=grouped_history(store, window=30))
    }
    assert from_store == expected
    assert from_store[NODE.format("flaky")][0] is True
    assert from_store[NODE.format("solid")][0] is False


def test_outcome_mapping(store):
    add_run(
        store, "r1", 1, {"a": "error", "b": "xfailed", "c": "not_run", "d": ["error", "passed"]}
    )
    recs = {(r.test_id.split("::")[-1], r.outcome) for r in history_from_store(store)}
    assert recs == {("a", "failed"), ("b", "skipped"), ("d", "rerun"), ("d", "passed")}
