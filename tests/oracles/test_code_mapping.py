"""Tests for oracles.code_mapping: expected codes read from a data file."""

from __future__ import annotations

import json

import pytest

from oracles.code_mapping import CodeMapping, CodeMatch, MigrationState


@pytest.fixture
def mapping(tmp_path):
    path = tmp_path / "plan-codes.json"
    path.write_text(
        json.dumps({"Standard cover": "PLN-110", "Extended  cover": "PLN-120", "Add-on": "PLN-900"})
    )
    return CodeMapping.load(path)


def test_lookup_normalises_whitespace_and_case(mapping):
    assert len(mapping) == 3
    assert mapping.expected_for("  standard   COVER ") == "PLN-110"
    assert mapping.expected_for("Extended cover") == "PLN-120"
    assert mapping.expected_for("Unknown") is None
    assert mapping.expected_for(None) is None


def test_mapped_terms_lists_actual_vs_expected(mapping):
    terms = [
        {"description": "Standard cover", "code": "PLN-110"},
        {"description": "Extended cover", "code": "OLD-7"},
        {"description": "Free text", "code": "X"},
        {"code": "no description"},
    ]
    mapped = mapping.mapped_terms("SUB-000123", terms)
    assert mapped == [
        CodeMatch("SUB-000123", "Standard cover", "PLN-110", "PLN-110"),
        CodeMatch("SUB-000123", "Extended cover", "OLD-7", "PLN-120"),
    ]
    assert [t.converted for t in mapped] == [True, False]
    assert "NOT CONVERTED" in str(mapped[1])
    assert mapping.state(mapped) is MigrationState.PARTIAL


def test_states(mapping):
    def terms(*codes):
        return mapping.mapped_terms(
            "E1",
            [
                {"description": d, "code": c}
                for d, c in zip(["Standard cover", "Add-on"], codes, strict=True)
            ],
        )

    assert mapping.state(terms("PLN-110", "PLN-900")) is MigrationState.MIGRATED
    assert mapping.state(terms("A", "B")) is MigrationState.UNMIGRATED
    assert mapping.state([]) is MigrationState.NOT_APPLICABLE


def test_custom_keys_and_numeric_codes():
    m = CodeMapping({"Basic": "10"})
    [t] = m.mapped_terms(
        "E", [{"label": "Basic", "plan": 10}], description_key="label", code_key="plan"
    )
    assert t.actual == "10" and t.converted


@pytest.mark.parametrize("content", ["[]", "{}", '{"a": 1}', '{"a": ""}'])
def test_bad_tables_are_rejected(tmp_path, content):
    path = tmp_path / "t.json"
    path.write_text(content)
    with pytest.raises(ValueError):
        CodeMapping.load(path)


def test_conflicting_duplicates_are_rejected():
    with pytest.raises(ValueError, match="maps to both"):
        CodeMapping({"Basic": "A", "basic ": "B"})
