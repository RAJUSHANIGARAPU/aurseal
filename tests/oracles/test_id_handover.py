"""Tests for the numbering hand-over rule: new numbers continue above migrated ones."""

from __future__ import annotations

from pathlib import Path

import pytest

from oracles.id_sequences import (
    IncompleteRead,
    handover_violations,
    parse_counter,
    require_complete,
)

ORIGIN = {
    "SUB-000101": "migrated",
    "SUB-000250": "migrated",
    "SUB-000251": "new",
    "SUB-000400": "new",
}


@pytest.fixture(autouse=True)
def isolated_verdicts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VERDICTS_FILE", str(tmp_path / "verdicts.jsonl"))
    monkeypatch.delenv("COVERAGE_VERDICT_REQUIRED", raising=False)


def test_new_numbers_above_the_highest_migrated_hold():
    assert handover_violations(ORIGIN, ORIGIN.get, "migrated", "new") == []


def test_a_new_number_at_or_below_the_migrated_maximum_is_named():
    origin = {**ORIGIN, "SUB-000250-B": "new", "SUB-000099": "new"}
    found = handover_violations(origin, origin.get, "migrated", "new")
    assert found == [
        "SUB-000250-B (new) is not above the highest migrated number SUB-000250",
        "SUB-000099 (new) is not above the highest migrated number SUB-000250",
    ]


def test_padding_does_not_decide_the_order():
    origin = {"SUB-9": "migrated", "SUB-000010": "new"}
    assert handover_violations(origin, origin.get, "migrated", "new") == []


@pytest.mark.parametrize("absent", ["migrated", "new"])
def test_abstains_when_an_origin_is_empty(absent):
    origin = {n: o for n, o in ORIGIN.items() if o != absent}
    with pytest.raises(pytest.skip.Exception, match=f"no '{absent}' numbers"):
        handover_violations(origin, origin.get, "migrated", "new")


def test_numbers_of_other_origins_are_ignored():
    origin = {**ORIGIN, "SUB-000001": "imported-by-hand"}
    assert handover_violations(origin, origin.get, "migrated", "new") == []


def test_require_complete():
    require_complete(4, 4)
    with pytest.raises(IncompleteRead, match="read 3 of 4"):
        require_complete(3, 4)


def test_parse_counter():
    assert parse_counter("ORD-EU-004211") == 4211
    assert parse_counter("17") == 17
    with pytest.raises(ValueError):
        parse_counter("SUB-")
