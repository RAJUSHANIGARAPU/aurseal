"""The detector shape, worked end to end on a neutral example.

A detector is three pieces, each checkable offline before it ever meets a live
system:

1. an **oracle** asserting the correct behaviour, failing with wording keyed to
   the defect (the signature);
2. a **positive control** proving the instrument read something real;
3. a **boundary sibling**: a neighbouring check that fails with *different*
   wording, so a near-miss can never be counted as the tracked defect.

The offline self-test pins all three against hand-built shapes of the data: the
fixed shape passes, the broken shape fails with the signature, the sibling's
failure does not match it, and wrapped in ``still_reproduces`` the broken shape
reads ``reproduces`` and the fixed shape ``cleared``.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from verification.defect_detector import still_reproduces
from verification.verdicts import RedVerdict, read_verdicts

TICKET = "BUG-142"
SIGNATURE = r"BUG-142: the discount was not applied to the order total"


def assert_discount_applied(order: dict) -> None:
    """Oracle: a discounted order's total is the line sum minus the discount."""
    expected = sum(Decimal(line) for line in order["lines"]) - Decimal(order["discount"])
    actual = Decimal(order["total"])
    assert actual == expected, (
        f"BUG-142: the discount was not applied to the order total ({actual} != {expected})"
    )


def assert_lines_present(order: dict) -> None:
    """Boundary sibling: lines vanishing is a different failure with different wording."""
    assert order["lines"], "order has no lines: the total cannot be judged"


def assert_order_was_read(order: dict) -> None:
    """Positive control: the read produced a real order, not an empty default."""
    assert order.get("id") and order.get("total") not in (None, ""), "order read back empty"


FIXED = {"id": "ORD-1", "lines": ["10.00", "5.00"], "discount": "1.50", "total": "13.50"}
BROKEN = {**FIXED, "total": "15.00"}
NO_LINES = {**FIXED, "lines": []}
EMPTY_READ = {"id": "", "lines": [], "discount": "0", "total": ""}


def detect(order: dict) -> RedVerdict:
    def oracle() -> None:
        assert_lines_present(order)
        assert_discount_applied(order)

    return still_reproduces(
        TICKET, SIGNATURE, oracle, exercised=lambda: assert_order_was_read(order)
    )


def test_oracle_passes_on_the_fixed_shape():
    assert_discount_applied(FIXED)


def test_oracle_fails_with_the_signature_on_the_broken_shape():
    with pytest.raises(AssertionError, match=SIGNATURE):
        assert_discount_applied(BROKEN)


def test_boundary_sibling_fails_with_other_wording():
    with pytest.raises(AssertionError) as caught:
        assert_lines_present(NO_LINES)
    assert "discount was not applied" not in str(caught.value)


def test_detector_reads_broken_as_reproduces_and_fixed_as_cleared():
    assert detect(BROKEN) is RedVerdict.REPRODUCES
    with pytest.raises(AssertionError, match="KNOWN DEFECT CLEARED"):
        detect(FIXED)


def test_the_sibling_failure_is_never_counted_as_the_defect():
    with pytest.raises(AssertionError, match="UNDOCUMENTED"):
        detect(NO_LINES)


def test_an_empty_read_is_not_a_reproduction():
    with pytest.raises(pytest.skip.Exception):
        detect(EMPTY_READ)
    assert [r["verdict"] for r in read_verdicts()] == ["held_empty"]
