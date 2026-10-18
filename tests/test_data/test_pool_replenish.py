"""Tests for test_data.pool.replenish: seed N entities and write a targets file."""

from __future__ import annotations

import itertools

import pytest

from test_data import seed_mode
from test_data.pool import (
    ReplenishError,
    replenish,
    replenish_settings,
)


def _seeder():
    counter = itertools.count(1)
    calls: list[bool] = []

    def seed(booked: bool) -> dict:
        calls.append(booked)
        return {"number": f"SUB-{next(counter):06d}"}

    return seed, calls


def test_replenish_writes_targets_file(tmp_path):
    seed, calls = _seeder()
    path = tmp_path / "targets" / "replenished.txt"
    result = replenish(3, seed, label=lambda e: e["number"], path=path)
    assert result.seeded == ("SUB-000001", "SUB-000002", "SUB-000003")
    assert result.failures == () and calls == [False, False, False]
    # the file is consumed by a later targets run exactly as written
    config = seed_mode.resolve({"SEED_MODE": "targets", "ENTITY_ALLOWLIST_FILE": str(path)})
    assert config.allowlist.identifiers == result.seeded


def test_booked_flag_reaches_the_seeder(tmp_path):
    seed, calls = _seeder()
    result = replenish(2, seed, booked=True, label=lambda e: e["number"], path=tmp_path / "t")
    assert calls == [True, True] and result.booked
    assert "first period booked" in (tmp_path / "t").read_text()


def test_failed_seeds_are_recorded_not_fatal(tmp_path):
    seq = iter([RuntimeError("timeout"), {"number": "SUB-7"}])

    def seed(booked):
        item = next(seq)
        if isinstance(item, Exception):
            raise item
        return item

    result = replenish(2, seed, label=lambda e: e["number"], path=tmp_path / "t")
    assert result.seeded == ("SUB-7",)
    assert result.failures == ("#1: RuntimeError: timeout",)


def test_nothing_seeded_fails(tmp_path):
    def seed(booked):
        raise ValueError("refused")

    with pytest.raises(ReplenishError, match="no entity seeded out of 2"):
        replenish(2, seed, path=tmp_path / "t")
    assert not (tmp_path / "t").exists()


def test_targets_mode_refuses(tmp_path):
    config = seed_mode.resolve({"SEED_MODE": "targets", "ENTITY_ALLOWLIST": "SUB-1"})
    seed, calls = _seeder()
    with pytest.raises(ReplenishError, match="never creates data"):
        replenish(1, seed, path=tmp_path / "t", config=config)
    assert calls == []


def test_count_must_be_positive(tmp_path):
    with pytest.raises(ReplenishError):
        replenish(0, lambda b: "x", path=tmp_path / "t")


def test_settings_from_env():
    assert replenish_settings({}) == (5, False)
    assert replenish_settings({"REPLENISH_COUNT": "8", "REPLENISH_BOOKED": "TRUE"}) == (8, True)
