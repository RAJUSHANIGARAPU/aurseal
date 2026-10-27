"""Declared defect ids: static scan, committed snapshot, floor, drift ratchet, batching."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from integrations.trackers.base import BadQuery, DefectState, TrackerConfig
from integrations.trackers.file import FileTracker
from traceability.cli import main as trace_main
from traceability.declared import (
    CLOSED,
    ENV,
    OPEN,
    TrackingConfig,
    age_line,
    check,
    drift_lines,
    scan,
)
from traceability.defects import Snapshot, lookup_states, refresh_declared

ROOT = Path(__file__).resolve().parents[2]

DETECTORS = """
import pytest
from verification.defect_detector import still_reproduces

ROUNDING = "BUG-12"

@pytest.mark.known_defect("BUG-10", reason="r")
def test_marked():
    pass

def test_detectors():
    still_reproduces(ROUNDING, "sig", oracle, exercised=None, closed=False)
    still_reproduces("BUG-13", "sig", oracle, exercised=None, closed=True)
    # still_reproduces("BUG-99", ...) in a comment is not a call site
    text = 'still_reproduces("BUG-98", "sig")'
"""


def _suite(tmp_path: Path, min_calls: int = 0) -> TrackingConfig:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_d.py").write_text(DETECTORS)
    (tmp_path / "tests" / "x.manifest.json").write_text(
        json.dumps([{"template": "/a", "open_issues": [{"issue": "BUG-20", "summary": "s"}]}])
    )
    (tmp_path / "defects").mkdir()
    (tmp_path / "defects" / "tracking.yaml").write_text(
        f"roots: [tests]\nmin_call_sites: {min_calls}\nsnapshot: defects/snap.json\n"
        "drift_baseline: defects/drift.txt\n"
    )
    return TrackingConfig.load(rootdir=tmp_path)


def _snap(**states: str) -> dict[str, DefectState]:
    return {k.replace("_", "-"): DefectState(k.replace("_", "-"), v) for k, v in states.items()}


def test_the_scan_reads_calls_constants_markers_and_manifests_not_comments(tmp_path):
    found = scan(_suite(tmp_path))
    assert found.ids == ["BUG-10", "BUG-12", "BUG-13", "BUG-20"]
    assert found.call_sites == 3
    assert found.expectations() == {
        "BUG-10": {ENV},
        "BUG-12": {OPEN},
        "BUG-13": {CLOSED},
        "BUG-20": {OPEN},
    }


def test_a_declared_id_missing_from_the_snapshot_fails(tmp_path):
    config = _suite(tmp_path)
    result = check(config, _snap(BUG_10="open", BUG_12="open", BUG_13="closed"))
    assert not result.ok
    assert any("BUG-20" in p and "defects sync" in p for p in result.problems)


def test_the_floor_catches_a_scan_that_found_too_little(tmp_path):
    # Anti-shrink control: a narrowed scan cannot pass by tracking nothing.
    config = _suite(tmp_path, min_calls=10)
    full = _snap(BUG_10="open", BUG_12="open", BUG_13="closed", BUG_20="open")
    assert any(p.startswith("FLOOR") for p in check(config, full).problems)


def test_drift_is_ratcheted_and_the_baseline_only_shrinks(tmp_path):
    config = _suite(tmp_path)
    snap = _snap(BUG_10="closed", BUG_12="closed", BUG_13="closed", BUG_20="open")
    assert drift_lines(scan(config), snap) == ["BUG-12 declared open tracker closed"]
    assert any(p.startswith("DRIFT: BUG-12") for p in check(config, snap).problems)
    (tmp_path / "defects" / "drift.txt").write_text(
        "# tolerated\nBUG-12 declared open tracker closed\nBUG-20 declared open tracker closed\n"
    )
    assert check(config, snap).problems == [
        "STALE DRIFT BASELINE LINE, delete it: BUG-20 declared open tracker closed"
    ]


def test_cli_check_and_refresh(tmp_path, capsys):
    _suite(tmp_path)
    trackers = tmp_path / "trackers.yaml"
    defects = tmp_path / "defects.yaml"
    defects.write_text("defects:\n  - {id: BUG-12, title: rounding, state: open}\n")
    trackers.write_text(
        f"trackers:\n  file:\n    type: file\n    path: {defects}\n    prefixes: [BUG]\n"
    )
    root = ["--rootdir", str(tmp_path)]
    assert trace_main(["defects", "check", *root]) == 1  # no snapshot committed yet
    assert trace_main(["defects", "refresh", "--trackers", str(trackers), *root]) == 0
    snap = Snapshot.load(tmp_path / "defects" / "snap.json")
    assert sorted(snap.defects) == ["BUG-10", "BUG-12", "BUG-13", "BUG-20"]
    assert snap.defects["BUG-12"].state == "open" and snap.defects["BUG-20"].state == "unknown"
    capsys.readouterr()
    assert trace_main(["defects", "check", *root]) == 0
    assert "days old" in capsys.readouterr().out


def test_without_a_configured_snapshot_it_is_not_measured(tmp_path, capsys):
    assert trace_main(["defects", "check", "--rootdir", str(tmp_path)]) == 0
    assert "not measured" in capsys.readouterr().out


def test_the_committed_snapshot_covers_every_declared_id():
    """The drift ratchet on the repository itself: offline, from committed files only."""
    config = TrackingConfig.load(rootdir=ROOT)
    snapshot = Snapshot.load(config.path(config.snapshot))
    result = check(config, snapshot.defects)
    assert result.ok, "\n".join(result.problems)


def test_age_line():
    assert age_line("2026-01-01T00:00:00Z").startswith("snapshot 2026-01-01 (")
    assert age_line("garbage") == "snapshot garbage (age unknown)"
    assert age_line(None) == "snapshot with no date (age unknown)"


class _Batching:
    def __init__(self, bad: set[str]):
        self.bad = bad
        self.batches: list[list[str]] = []

    def get_states_batch(self, ids):
        self.batches.append(list(ids))
        if self.bad & set(ids):
            raise BadQuery("bad id")
        return {i: DefectState(i, "open") for i in ids}


def test_a_rejected_batch_is_bisected_and_the_lone_bad_id_is_missing():
    tracker = _Batching({"BUG-5"})
    missing: list[str] = []
    ids = [f"BUG-{i}" for i in range(1, 9)]
    got = lookup_states(tracker, ids, missing, size=4)
    assert missing == ["BUG-5"]
    assert sorted(got) == sorted(i for i in ids if i != "BUG-5")
    assert tracker.batches[0] == ids[:4] and tracker.batches[1] == ids[4:]


def test_the_file_tracker_rejects_a_malformed_batch(tmp_path):
    tracker = FileTracker(tmp_path / "none.yaml")
    with pytest.raises(BadQuery):
        tracker.get_states_batch(["BUG-1", "BUG-x"])
    missing: list[str] = []
    got = lookup_states(tracker, ["BUG-1", "BUG-x"], missing)
    assert missing == ["BUG-x"] and got["BUG-1"].state == "unknown"


def test_refresh_keeps_only_the_declared_ids(tmp_path):
    config = TrackerConfig.from_body({"trackers": {}})
    out = tmp_path / "snap.json"
    out.write_text(json.dumps({"defects": {"BUG-1": {"id": "BUG-1", "state": "open"}}}))
    snap = refresh_declared(config, ["BUG-2"], out)
    assert list(snap.defects) == ["BUG-2"]
