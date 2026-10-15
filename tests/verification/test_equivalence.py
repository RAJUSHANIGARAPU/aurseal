"""Tests for verification.equivalence and the path matcher it relies on."""

from __future__ import annotations

from datetime import date

import pytest

from verification import _paths
from verification._temporal import Shift, add_months
from verification.equivalence import (
    ABSENT,
    DiffKind,
    EquivalenceOracle,
    Tolerance,
    assert_equivalent,
)


def _result(**overrides):
    base = {
        "id": "run-1",
        "generated_at": "2026-01-01T10:00:00Z",
        "total": 100.0,
        "items": [
            {"id": 11, "version": 3, "amount": 40.0, "due": "2026-01-31", "active": True},
            {"id": 12, "version": 1, "amount": 60.0, "due": "2026-02-28", "active": True},
        ],
    }
    base.update(overrides)
    return base


# ------------------------------------------------------------------
# path patterns
# ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pattern", "path", "expected"),
    [
        ("items[*].id", ("items", 0, "id"), True),
        ("items[*].id", ("items", 0, "amount"), False),
        ("**.id", ("id",), True),
        ("**.id", ("items", 3, "id"), True),
        ("items.*", ("items", 1), True),
        ("items[1]", ("items", 0), False),
        ("a.b", ("a", "b", "c"), False),
    ],
)
def test_pattern_matching(pattern, path, expected):
    assert _paths.matches(pattern, path) is expected


def test_brackets_are_not_character_classes():
    # fnmatch would read "[*]" as "the character *" and match "items*.id".
    assert not _paths.matches("items[*].id", ("items*", "id"))


def test_malformed_pattern_rejected_at_construction():
    with pytest.raises(ValueError):
        EquivalenceOracle(ignore=["items[x"])


def test_resolve_and_render_roundtrip():
    doc = _result()
    assert _paths.resolve(doc, "items[1].amount") == 60.0
    assert _paths.render(("items", 1, "amount")) == "items[1].amount"
    with pytest.raises(KeyError):
        _paths.resolve(doc, "items[5].amount")


def test_add_months_clamps_to_month_end():
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2026, 11, 15), 3) == date(2027, 2, 15)


# ------------------------------------------------------------------
# oracle: positive cases (must stay green)
# ------------------------------------------------------------------


def test_identical_results_are_equivalent():
    report = EquivalenceOracle().compare(_result(), _result())
    assert report.equivalent
    assert report.compared > 0


def test_ignored_volatile_fields_do_not_differ_but_are_reported():
    oracle = EquivalenceOracle(ignore=["id", "generated_at", "**.version", "items[*].id"])
    right = _result(id="run-2", generated_at="2026-05-05T00:00:00Z")
    right["items"][0]["version"] = 9
    report = oracle.compare(_result(), right)
    assert report.equivalent
    assert "items[0].version" in report.ignored and "generated_at" in report.ignored


def test_numeric_tolerance_accepts_rounding_noise():
    oracle = EquivalenceOracle(tolerances={"**.amount": Tolerance(abs_tol=0.01)})
    right = _result()
    right["items"][0]["amount"] = 40.004
    assert oracle.compare(_result(), right).equivalent


def test_date_shift_rule_accepts_expected_offset():
    oracle = EquivalenceOracle(date_shifts={"items[*].due": Shift(months=1)})
    right = _result()
    right["items"][0]["due"] = "2026-02-28"  # Jan 31 + 1 month, clamped
    right["items"][1]["due"] = "2026-03-28"
    assert oracle.compare(_result(), right).equivalent


# ------------------------------------------------------------------
# oracle: negative cases (must bite)
# ------------------------------------------------------------------


def test_value_beyond_tolerance_is_reported_with_rule():
    oracle = EquivalenceOracle(tolerances={"**.amount": Tolerance(abs_tol=0.01)})
    right = _result()
    right["items"][1]["amount"] = 60.5
    (diff,) = oracle.compare(_result(), right).differences
    assert diff.path == "items[1].amount"
    assert (diff.left, diff.right, diff.kind) == (60.0, 60.5, DiffKind.VALUE)
    assert "abs=0.01" in diff.rule


def test_tolerance_is_scoped_to_its_path():
    oracle = EquivalenceOracle(tolerances={"items[*].amount": Tolerance(abs_tol=1)})
    report = oracle.compare(_result(), _result(total=100.5))
    assert [d.path for d in report.differences] == ["total"]


def test_wrong_date_shift_is_reported_with_expected_value():
    oracle = EquivalenceOracle(date_shifts={"items[*].due": Shift(months=1)})
    right = _result()
    right["items"][0]["due"] = "2026-03-03"  # naive +31 days instead of calendar month
    right["items"][1]["due"] = "2026-03-28"
    (diff,) = oracle.compare(_result(), right).differences
    assert diff.path == "items[0].due"
    assert "expected 2026-02-28" in diff.rule


def test_missing_keys_and_length_mismatch_are_reported():
    right = _result()
    del right["total"]
    right["extra"] = 1
    right["items"] = right["items"][:1]
    kinds = {(d.path, d.kind) for d in EquivalenceOracle().compare(_result(), right).differences}
    assert ("total", DiffKind.MISSING_RIGHT) in kinds
    assert ("extra", DiffKind.MISSING_LEFT) in kinds
    assert ("items", DiffKind.LENGTH) in kinds


def test_bool_is_not_number():
    right = _result()
    right["items"][0]["active"] = 1
    (diff,) = EquivalenceOracle().compare(_result(), right).differences
    assert diff.kind is DiffKind.TYPE


def test_assert_helper_fails_with_readable_table():
    right = _result(total=99.0)
    del right["items"][0]["due"]
    with pytest.raises(AssertionError) as excinfo:
        assert_equivalent(_result(), right, left_label="path_a", right_label="path_b")
    message = str(excinfo.value)
    assert message.startswith("not equivalent: 2 difference(s)")
    assert "path_a" in message and "path_b" in message
    assert "total" in message and "100.0" in message and "99.0" in message
    assert "items[0].due" in message and repr(ABSENT) in message


def test_assert_helper_returns_report_when_equivalent():
    assert assert_equivalent(_result(), _result()).equivalent
