"""The structured, bounded heal event log."""

from __future__ import annotations

import json

from autonomous_ui.heal_events import DEFAULT_PATH, HealEvent, HealEventLog, default_path


def _event(i: int = 0, **kw) -> HealEvent:
    return HealEvent(test=f"test_{i}", strategy="none", applied=False, details="x", **kw)


def test_appends_one_json_object_per_line(tmp_path):
    log = HealEventLog(tmp_path / "healing" / "events.jsonl")
    log.append(
        _event(
            locator_key="search",
            old_locator={"type": "testid", "value": "a"},
            new_locator={"type": "role", "role": "searchbox"},
            persisted=False,
            patched_files=["ui/locators/x.heals-pending.json"],
        )
    )
    log.append(_event(1))
    lines = (tmp_path / "healing" / "events.jsonl").read_text().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["locator_key"] == "search"
    assert first["persisted"] is False
    assert first["timestamp"]
    assert [e["test"] for e in log.read()] == ["test_0", "test_1"]


def test_bounded_to_the_newest_events(tmp_path):
    log = HealEventLog(tmp_path / "events.jsonl", max_events=3, prune_every=2)
    for i in range(7):
        log.append(_event(i))
    kept = [e["test"] for e in log.read()]
    assert len(kept) <= 4
    assert kept[-1] == "test_6"
    log.prune()
    assert [e["test"] for e in log.read()] == ["test_4", "test_5", "test_6"]


def test_malformed_lines_are_skipped_and_missing_file_is_empty(tmp_path):
    path = tmp_path / "events.jsonl"
    assert HealEventLog(path).read() == []
    path.write_text('{"test": "a"}\nnot json\n\n{"test": "b"}\n')
    assert [e["test"] for e in HealEventLog(path).read()] == ["a", "b"]


def test_unwritable_location_does_not_raise(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("")
    HealEventLog(blocker / "events.jsonl").append(_event())  # parent is a file


def test_path_from_environment(monkeypatch, tmp_path):
    monkeypatch.delenv("HEAL_EVENTS_FILE", raising=False)
    assert default_path() == DEFAULT_PATH
    monkeypatch.setenv("HEAL_EVENTS_FILE", str(tmp_path / "e.jsonl"))
    assert HealEventLog().path == tmp_path / "e.jsonl"
