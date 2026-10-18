"""Tests for oracles.id_sequences."""

from __future__ import annotations

import re

from oracles.id_sequences import (
    BusinessNumber,
    Issued,
    check_sequence,
    parse,
    shared_numbers,
    strict_increase_violations,
    unstable_entities,
)


class TestParse:
    def test_with_and_without_segment(self):
        assert parse("SUB-000123") == BusinessNumber("SUB-000123", "SUB", None, 123)
        assert parse("ORD-EU-004211") == BusinessNumber("ORD-EU-004211", "ORD", "EU", 4211)

    def test_counter_may_outgrow_the_padding(self):
        assert parse("SUB-1234567").counter == 1234567

    def test_rejections(self):
        assert parse(None) is None
        assert parse("sub-000123") is None
        assert parse("SUB-00123") is None  # shorter than padded
        assert parse("SUB-0123456") is None  # longer and still zero-padded
        assert parse("SUB-000123", expected_prefix="ORD") is None

    def test_custom_pattern_and_length(self):
        pattern = re.compile(r"^(?P<prefix>INV)(?P<counter>\d+)$")
        assert parse("INV0042", pattern=pattern, padded_length=4).counter == 42


def _n(raw):
    return parse(raw)


def test_strict_increase_per_series():
    issued = [
        _n("SUB-000010"),
        _n("ORD-000001"),
        _n("SUB-000015"),
        _n("SUB-000015"),
        _n("SUB-000014"),
        _n("ORD-000002"),
    ]
    assert strict_increase_violations(issued) == [
        "SUB-000015 issued after SUB-000015",
        "SUB-000014 issued after SUB-000015",
    ]


def test_shared_and_unstable():
    assert shared_numbers({"e1": "SUB-000001", "e2": "SUB-000001", "e3": "SUB-000002"}) == {
        "SUB-000001": ["e1", "e2"]
    }
    assert unstable_entities({"e1": ["SUB-000001", "SUB-000001"], "e2": ["A", "B"]}) == {
        "e2": ["A", "B"]
    }


def test_check_sequence_reports_everything():
    report = check_sequence(
        [
            Issued("e1", ["SUB-000100", "SUB-000100"]),
            Issued("e2", ["SUB-000105", "SUB-000107"]),
            Issued("e3", ["SUB-000105"]),
            Issued("e4", ["bad"]),
            Issued("e5", []),
        ],
        expected_prefix="SUB",
    )
    assert not report.ok
    assert report.malformed == ["e4: 'bad'", "e5: no number"]
    assert report.increase_violations == ["SUB-000105 issued after SUB-000105"]
    assert report.shared == {"SUB-000105": ["e2", "e3"]}
    assert report.unstable == {"e2": ["SUB-000105", "SUB-000107"]}
    assert len(report.problems()) == 5


def test_check_sequence_clean():
    report = check_sequence([Issued("a", ["SUB-000001"]), Issued("b", ["SUB-000009"])])
    assert report.ok and report.problems() == []
