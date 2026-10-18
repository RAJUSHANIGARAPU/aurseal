"""LocatorStore: atomic writes, heal metadata, propose mode and reviewed apply."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from autonomous_ui.locator_store import (
    LocatorStore,
    autopersist_from_env,
    pending_path_for,
    strip_heal_metadata,
)
from core.locator_strategy import render

WHEN = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
ORIGINAL = {
    "search": {"type": "testid", "value": "search-field"},
    "go": {"type": "text", "value": "Go"},
}


@pytest.fixture
def registry(tmp_path):
    path = tmp_path / "app_locators.json"
    path.write_text(json.dumps(ORIGINAL, indent=2))
    return path


def test_autopersist_writes_the_registry_with_metadata(registry):
    store = LocatorStore(registry, autopersist=True)
    write = store.update("search", {"type": "role", "role": "searchbox"}, healed_at=WHEN)
    assert write.persisted and write.path == registry
    on_disk = json.loads(registry.read_text())
    assert on_disk["search"] == {
        "type": "role",
        "role": "searchbox",
        "last_healed": "2026-01-02T03:04:05+00:00",
        "healed_from": ORIGINAL["search"],
    }
    assert on_disk["go"] == ORIGINAL["go"]
    assert registry.read_text().endswith("\n")
    assert not pending_path_for(registry).exists()


def test_propose_mode_leaves_the_registry_and_files_a_proposal(registry):
    before = registry.read_text()
    store = LocatorStore(registry, autopersist=False)
    write = store.update("search", {"type": "role", "role": "searchbox"}, reason="moved")
    assert not write.persisted
    assert write.path == registry.with_name("app_locators.heals-pending.json")
    assert registry.read_text() == before
    proposal = store.pending()["search"]
    assert proposal["current"] == ORIGINAL["search"]
    assert proposal["reason"] == "moved"
    # the in-memory view follows the heal in both modes
    assert strip_heal_metadata(store.get("search")) == {"type": "role", "role": "searchbox"}


def test_apply_pending_moves_reviewed_proposals(registry):
    store = LocatorStore(registry, autopersist=False)
    store.update("search", {"type": "role", "role": "searchbox"})
    store.update("go", {"type": "role", "role": "button", "name": "Go"})
    assert store.apply_pending(["go"]) == ["go"]
    on_disk = json.loads(registry.read_text())
    assert on_disk["search"] == ORIGINAL["search"]
    assert on_disk["go"]["role"] == "button"
    assert list(store.pending()) == ["search"]
    assert store.apply_pending() == ["search"]
    assert not store.pending_path.exists()
    assert store.apply_pending() == []


def test_stale_proposal_is_not_applied(registry):
    store = LocatorStore(registry, autopersist=False)
    store.update("search", {"type": "role", "role": "searchbox"})
    LocatorStore(registry, autopersist=True).update("search", {"type": "label", "value": "Search"})
    assert store.apply_pending() == []
    assert json.loads(registry.read_text())["search"]["type"] == "label"


def test_heal_metadata_does_not_disturb_rendering(registry):
    LocatorStore(registry, autopersist=True).update("go", {"type": "placeholder", "value": "Go"})
    entry = json.loads(registry.read_text())["go"]

    class _Root:
        def get_by_placeholder(self, value):
            return ("placeholder", value)

    assert render(_Root(), entry) == ("placeholder", "Go")


def test_unknown_key_and_missing_registry(tmp_path, registry):
    with pytest.raises(KeyError, match="nope"):
        LocatorStore(registry, autopersist=True).update("nope", {"type": "text", "value": "x"})
    with pytest.raises(FileNotFoundError):
        LocatorStore(tmp_path / "absent.json").load()


def test_file_mode_is_preserved(registry):
    registry.chmod(0o640)
    LocatorStore(registry, autopersist=True).update("go", {"type": "text", "value": "Next"})
    assert registry.stat().st_mode & 0o777 == 0o640


@pytest.mark.parametrize(
    ("value", "expected"), [("true", True), ("1", True), ("", False), ("no", False)]
)
def test_autopersist_flag(value, expected):
    assert autopersist_from_env({"HEAL_AUTOPERSIST": value}) is expected
    assert autopersist_from_env({}) is False
