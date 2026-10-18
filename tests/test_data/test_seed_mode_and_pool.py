"""Tests for test_data.seed_mode and test_data.pool."""

from __future__ import annotations

import pytest

from test_data import seed_mode as sm
from test_data.pool import NoTarget, ReusePool, select_targets

pytest_plugins = ["pytester"]


class TestResolve:
    def test_default_is_fresh(self):
        cfg = sm.resolve({})
        assert cfg.mode is sm.SeedMode.FRESH and not cfg.allowlist and cfg.may_build

    @pytest.mark.parametrize("raw", ["reuse", "REUSE", " reuse "])
    def test_mode_is_case_and_space_insensitive(self, raw):
        assert sm.resolve({"SEED_MODE": raw}).mode is sm.SeedMode.REUSE

    def test_legacy_reuse_flag(self):
        assert sm.resolve({"SEED_REUSE_ENABLED": "true"}).mode is sm.SeedMode.REUSE

    def test_targets_with_inline_list(self):
        cfg = sm.resolve({"SEED_MODE": "targets", "ENTITY_ALLOWLIST": "A-1, B-2;A-1"})
        assert cfg.allowlist.identifiers == ("A-1", "B-2") and not cfg.may_build
        assert "B-2" in cfg.allowlist and "C-3" not in cfg.allowlist

    def test_reuse_may_be_narrowed_by_an_allowlist(self):
        cfg = sm.resolve({"SEED_MODE": "reuse", "ENTITY_ALLOWLIST": "A-1"})
        assert cfg.mode is sm.SeedMode.REUSE and "A-1" in cfg.allowlist

    @pytest.mark.parametrize(
        "env,needle",
        [
            ({"SEED_MODE": "targets"}, "needs"),
            ({"ENTITY_ALLOWLIST": "A-1"}, "fresh"),
            ({"SEED_MODE": "fresh", "ENTITY_ALLOWLIST_FILE": "x.txt"}, "fresh"),
            (
                {"SEED_MODE": "targets", "SEED_REUSE_ENABLED": "true", "ENTITY_ALLOWLIST": "A"},
                "contra",
            ),
            ({"SEED_MODE": "sometimes"}, "not a valid seed mode"),
            ({"SEED_MODE": "targets", "ENTITY_ALLOWLIST": "ok, not/valid"}, "not a valid"),
        ],
    )
    def test_contradictions_fail(self, env, needle):
        with pytest.raises(sm.SeedConfigError, match=needle):
            sm.resolve(env)

    def test_file_allowlist_with_comments_merges_after_inline(self, tmp_path):
        f = tmp_path / "targets.txt"
        f.write_text("# picked by hand\nB-2 C-3 # second\n\nA-1\n")
        cfg = sm.resolve(
            {"SEED_MODE": "targets", "ENTITY_ALLOWLIST": "A-1", "ENTITY_ALLOWLIST_FILE": str(f)}
        )
        assert cfg.allowlist.identifiers == ("A-1", "B-2", "C-3")
        assert "ENTITY_ALLOWLIST_FILE" in cfg.allowlist.source

    def test_file_that_adds_nothing_fails(self, tmp_path):
        f = tmp_path / "t.txt"
        f.write_text("# empty\nA-1\n")
        with pytest.raises(sm.SeedConfigError, match="names no identifier"):
            sm.resolve(
                {"SEED_MODE": "targets", "ENTITY_ALLOWLIST": "A-1", "ENTITY_ALLOWLIST_FILE": str(f)}
            )

    def test_unreadable_file_fails(self, tmp_path):
        with pytest.raises(sm.SeedConfigError, match="cannot be read"):
            sm.resolve({"SEED_MODE": "targets", "ENTITY_ALLOWLIST_FILE": str(tmp_path / "nope")})


ITEMS = [
    {"id": "A-1", "booked": 2, "open_draft": False},
    {"id": "B-2", "booked": 0, "open_draft": False},
    {"id": "C-3", "booked": 1, "open_draft": True},
    {"id": "D-4", "booked": 1, "open_draft": False},
]


def _pool(items=ITEMS):
    return ReusePool(
        lambda: list(items),
        fit=lambda e: e["booked"] > 0 and not e["open_draft"],
        label=lambda e: e["id"],
        preference=lambda e: e["booked"],  # partly booked first
    )


class TestPool:
    def test_find_filters_by_shape_and_orders_by_preference(self):
        assert [e["id"] for e in _pool().find(5)] == ["D-4", "A-1"]
        assert [e["id"] for e in _pool().find(1)] == ["D-4"]

    def test_fresh_never_lists_the_pool(self):
        listed = []
        pool = ReusePool(lambda: listed.append(1) or [], fit=bool, label=str)
        targets = select_targets(sm.resolve({}), pool, lambda: "NEW-1")
        assert listed == [] and targets[0].was_seeded and targets[0].label == "NEW-1"

    def test_reuse_prefers_the_pool(self):
        targets = select_targets(sm.resolve({"SEED_MODE": "reuse"}), _pool(), None, limit=2)
        assert [(t.label, t.was_seeded) for t in targets] == [("D-4", False), ("A-1", False)]

    def test_reuse_falls_back_to_seeding_when_dry(self):
        seeded = {"id": "N-9", "booked": 1, "open_draft": False}
        targets = select_targets(sm.resolve({"SEED_MODE": "reuse"}), _pool([]), lambda: seeded)
        assert targets[0].was_seeded and targets[0].label == "N-9"

    def test_targets_only_considers_the_allowlist_and_never_builds(self):
        cfg = sm.resolve({"SEED_MODE": "targets", "ENTITY_ALLOWLIST": "A-1,B-2"})
        assert [t.label for t in select_targets(cfg, _pool(), None, limit=3)] == ["A-1"]
        cfg = sm.resolve({"SEED_MODE": "targets", "ENTITY_ALLOWLIST": "B-2"})
        with pytest.raises(NoTarget, match="never builds"):
            select_targets(cfg, _pool(), lambda: pytest.fail("must not seed"))

    def test_no_seed_path_is_a_clear_no_target(self):
        with pytest.raises(NoTarget, match="no seed path"):
            select_targets(sm.resolve({}), _pool(), None)


def test_seed_config_fixture_turns_a_contradiction_into_a_usage_error(pytester, monkeypatch):
    monkeypatch.setenv("SEED_MODE", "targets")
    monkeypatch.delenv("ENTITY_ALLOWLIST", raising=False)
    monkeypatch.delenv("ENTITY_ALLOWLIST_FILE", raising=False)
    pytester.makepyfile("def test_x(seed_config):\n    pass\n")
    result = pytester.runpytest("-p", "test_data.pytest_plugin")
    assert result.ret != 0
    result.stdout.fnmatch_lines(["*SEED_MODE=targets needs*"])
