"""Defect status: snapshot fallback, unseen ids open, the environment override wins."""

from __future__ import annotations

from integrations.trackers.base import DefectState, TrackerConfig, TrackerUnavailable
from traceability.defects import Snapshot, StatusCache, open_defects_from_snapshot
from verification.defect_detector import closed_defects

CONFIG = TrackerConfig.from_body(
    {
        "trackers": {
            "f": {"type": "file", "prefixes": ["BUG"]},
            "g": {"type": "file", "prefixes": ["GH"]},
        }
    }
)


class FakeTracker:
    def __init__(self, states, fail=False):
        self.states, self.fail, self.asked = states, fail, []

    def get_states(self, ids):
        self.asked.extend(ids)
        if self.fail:
            raise TrackerUnavailable("down")
        return {i: self.states.get(i, DefectState(i, "unknown")) for i in ids}

    def list_open(self, labels=()):
        if self.fail:
            raise TrackerUnavailable("down")
        return [s for s in self.states.values() if s.state == "open"]


def _cache(tmp_path, trackers):
    return StatusCache(CONFIG, tmp_path / "snap.json", factory=lambda n, s, sess: trackers[n])


def test_sync_writes_states_and_open_list(tmp_path):
    trackers = {
        "f": FakeTracker(
            {
                "BUG-1": DefectState("BUG-1", "closed"),
                "BUG-2": DefectState("BUG-2", "open", severity="high"),
            }
        ),
        "g": FakeTracker({}),
    }
    snap = _cache(tmp_path, trackers).sync(["BUG-1", "GH-9"])
    assert trackers["f"].asked == ["BUG-1"] and trackers["g"].asked == ["GH-9"]
    assert snap.closed_ids() == {"BUG-1"} and snap.open_ids == ["BUG-2"]
    assert open_defects_from_snapshot(tmp_path / "snap.json")()[0]["severity"] == "high"


def test_an_unreachable_tracker_keeps_the_last_snapshot(tmp_path):
    first = {"f": FakeTracker({"BUG-1": DefectState("BUG-1", "closed")}), "g": FakeTracker({})}
    _cache(tmp_path, first).sync(["BUG-1"])
    down = {"f": FakeTracker({}, fail=True), "g": FakeTracker({})}
    snap = _cache(tmp_path, down).sync(["BUG-1"])
    assert snap.closed_ids() == {"BUG-1"}
    assert any("kept the last snapshot" in e for e in snap.errors)


def test_unseen_ids_are_open_and_the_env_override_wins(tmp_path, monkeypatch):
    trackers = {"f": FakeTracker({"BUG-1": DefectState("BUG-1", "closed")}), "g": FakeTracker({})}
    cache = _cache(tmp_path, trackers)
    cache.sync(["BUG-1"])
    assert cache.is_open("BUG-404") and not cache.is_open("BUG-1")
    monkeypatch.setenv("KNOWN_DEFECTS_CLOSED", "BUG-404")
    assert not cache.is_open("BUG-404")


def test_the_verdict_layer_reads_the_snapshot_only_when_pointed_at_it(tmp_path, monkeypatch):
    trackers = {"f": FakeTracker({"BUG-1": DefectState("BUG-1", "closed")}), "g": FakeTracker({})}
    _cache(tmp_path, trackers).sync(["BUG-1"])
    monkeypatch.delenv("DEFECT_STATUS_FILE", raising=False)
    monkeypatch.setenv("KNOWN_DEFECTS_CLOSED", "BUG-7")
    assert closed_defects() == {"BUG-7"}
    monkeypatch.setenv("DEFECT_STATUS_FILE", str(tmp_path / "snap.json"))
    assert closed_defects() == {"BUG-7", "BUG-1"}
    (tmp_path / "snap.json").write_text("not json")
    assert closed_defects() == {"BUG-7"}


def test_no_snapshot_is_an_error_for_the_open_list(tmp_path):
    import pytest

    with pytest.raises(FileNotFoundError):
        open_defects_from_snapshot(tmp_path / "none.json")()
    assert Snapshot.load(tmp_path / "none.json").defects == {}
