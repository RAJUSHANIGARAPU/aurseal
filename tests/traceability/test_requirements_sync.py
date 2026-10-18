"""Sync: diff categories, orphaned links, conflicting duplicates, dry run, snapshot."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest

from results.api import ApiError, dispatch
from results.store import open_store
from traceability import api  # noqa: F401 - registers the routes
from traceability.cli import main as trace_main
from traceability.links import CaseLink
from traceability.requirements import Registry, Requirement
from traceability.requirements_sync import DuplicateRequirement, merge, sync


@dataclass
class Src:
    name: str
    items: list = field(default_factory=list)
    skipped: list = field(default_factory=list)

    def fetch(self):
        return list(self.items)


OLD = Registry.from_requirements(
    [
        Requirement("REQ-1", "one", priority="must"),
        Requirement("REQ-2", "two"),
        Requirement("REQ-3", "three"),
        Requirement("REQ-4", "four"),
    ]
)
NEW = [
    Requirement("REQ-1", "one, reworded", priority="must"),
    Requirement("REQ-2", "two", priority="should"),
    Requirement("REQ-3", "three", status="retired"),
    Requirement("REQ-5", "five"),
]
LINKS = [CaseLink("t::a", ("REQ-3",)), CaseLink("t::b", ("REQ-4", "REQ-1"))]


def test_diff_categories_and_orphans(tmp_path):
    snap = tmp_path / "snapshot.json"
    report = sync([Src("local", NEW)], snap, LINKS, previous=OLD)
    assert report.added == ["REQ-5"]
    assert report.retitled == [{"id": "REQ-1", "old": "one", "new": "one, reworded"}]
    assert report.changed == ["REQ-1", "REQ-2"]
    assert report.retired == ["REQ-3"] and report.vanished == ["REQ-4"]
    assert report.orphaned_links == [
        {"nodeid": "t::a", "requirement_id": "REQ-3", "why": "retired"},
        {"nodeid": "t::b", "requirement_id": "REQ-4", "why": "vanished"},
    ]
    assert not snap.exists()  # dry run writes nothing
    md = report.to_markdown()
    assert "Orphaned links" in md and "one → one, reworded" in md


def test_write_produces_a_snapshot_the_registry_prefers(tmp_path):
    (tmp_path / "requirements").mkdir()
    (tmp_path / "requirements" / "requirements.yaml").write_text(
        "requirements:\n  - {id: REQ-1, title: yaml}\n"
    )
    snap = tmp_path / "requirements" / "snapshot.json"
    sync([Src("github", NEW)], snap, previous=OLD, write=True)
    reg = Registry.load(rootdir=tmp_path)
    assert reg.path == snap and reg.status("REQ-3") == "retired" and len(reg) == 4
    assert json.loads(snap.read_text())["requirements"][0]["content_hash"]


def test_identical_duplicates_merge_conflicting_ones_raise():
    a = Requirement("REQ-1", "one", source="local")
    b = Requirement("REQ-1", "one", source="github")
    assert [r.source for r in merge(("local", [a]), ("github", [b]))] == ["local"]
    c = Requirement("REQ-1", "different")
    with pytest.raises(DuplicateRequirement, match="local and github"):
        merge(("local", [a]), ("github", [c]))


def test_cli_dry_run_pr_body_and_store(tmp_path, capsys):
    local = tmp_path / "r.yaml"
    local.write_text("requirements:\n  - {id: REQ-1, title: one}\n  - {id: REQ-2, title: two}\n")
    snap = tmp_path / "snapshot.json"
    body = tmp_path / "pr.md"
    db = tmp_path / "r.db"
    args = [
        "requirements",
        "sync",
        "--local",
        str(local),
        "--snapshot",
        str(snap),
        "--no-collect",
        "--pr-body",
        str(body),
        "--db",
        str(db),
    ]
    assert trace_main([*args, "--write"]) == 0 and snap.exists()
    assert "**Added:** REQ-1, REQ-2" in body.read_text()
    first = snap.read_text()
    local.write_text("requirements:\n  - {id: REQ-1, title: one}\n  - {id: REQ-3, title: new}\n")
    assert trace_main(args) == 0 and snap.read_text() == first  # dry run writes nothing
    assert "dry run" in capsys.readouterr().out
    assert "**Added:** REQ-3" in body.read_text()
    assert trace_main([*args, "--write"]) == 0
    with open_store(str(db)) as store:
        latest = dispatch(store, "GET", "/requirements/syncs/latest")
        assert latest["added"] == ["REQ-3"] and latest["vanished"] == ["REQ-2"]
        listed = dispatch(store, "GET", "/requirements", params={"status": "active"})
        assert [r["id"] for r in listed["requirements"]] == ["REQ-1", "REQ-3"]
        assert dispatch(store, "GET", "/requirements/REQ-3")["title"] == "new"
        with pytest.raises(ApiError):
            dispatch(store, "GET", "/requirements/REQ-9")


def test_show(capsys):
    assert trace_main(["requirements", "show"]) == 0
    assert "REQ-1" in capsys.readouterr().out
