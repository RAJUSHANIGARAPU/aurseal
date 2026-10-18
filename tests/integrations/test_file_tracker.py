"""The YAML file tracker and the prefix map."""

from __future__ import annotations

import pytest

from integrations.trackers.base import DefectDraft, TrackerConfig, resolve
from integrations.trackers.file import FileTracker


def test_states_open_list_and_create(tmp_path):
    path = tmp_path / "defects.yaml"
    path.write_text(
        "defects:\n"
        "  - {id: BUG-1, title: one, state: open, severity: high, labels: [billing]}\n"
        "  - {id: BUG-2, title: two, state: closed}\n"
    )
    t = FileTracker(path)
    states = t.get_states(["BUG-1", "BUG-2", "BUG-9"])
    assert [states[i].state for i in ("BUG-1", "BUG-2", "BUG-9")] == ["open", "closed", "unknown"]
    assert [d.id for d in t.list_open()] == ["BUG-1"]
    assert t.list_open(["other"]) == []
    ref = t.create(DefectDraft("three", "b", "fp1", ("bug",)))
    assert ref.id == "BUG-3" and t.find_by_fingerprint("fp1").id == "BUG-3"


def test_a_missing_file_has_no_defects(tmp_path):
    assert FileTracker(tmp_path / "nope.yaml").list_open() == []


def test_prefix_map(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "octo/shop")
    cfg_path = tmp_path / "trackers.yaml"
    cfg_path.write_text(
        "trackers:\n  f: {type: file, path: d.yaml, prefixes: [BUG]}\n"
        "  g: {type: github, repo: '${GITHUB_REPOSITORY}', prefixes: [GH]}\n"
    )
    cfg = TrackerConfig.load(cfg_path, rootdir=tmp_path)
    assert cfg.tracker_for("BUG-4") == "f" and cfg.tracker_for("GH-4") == "g"
    assert cfg.tracker_for("REQ-4") is None
    assert cfg.trackers["g"]["repo"] == "octo/shop"
    assert resolve("GH-1", cfg).repo == "octo/shop"
    assert resolve("XX-1", cfg) is None


@pytest.mark.parametrize(
    "body,error",
    [
        ({"trackers": {"f": {"type": "file", "prefixes": ["REQ"]}}}, "requirement prefix"),
        ({"trackers": {"f": {"type": "jira"}}}, "type must be"),
        (
            {
                "trackers": {
                    "a": {"type": "file", "prefixes": ["B"]},
                    "b": {"type": "file", "prefixes": ["B"]},
                }
            },
            "two trackers",
        ),
    ],
)
def test_bad_config(body, error):
    with pytest.raises(ValueError, match=error):
        TrackerConfig.from_body(body)


def test_the_repository_config_loads():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    cfg = TrackerConfig.load(root / "integrations" / "trackers.yaml", rootdir=root)
    assert set(cfg.prefixes) == {"BUG", "GH"}
