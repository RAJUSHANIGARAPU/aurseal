"""Offline self-tests of the reseller-terms oracle and its fixtures."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from tests.demo_examples.finance import oracles
from verification.defect_detector import FIXED_TAG, still_reproduces
from verification.verdicts import RedVerdict

FIXTURES = Path(__file__).parent / "fixtures"


def test_share_scale_instrument_self_test():
    read = oracles.read_share
    assert read({"share_percent": 6}) == Decimal(6)
    assert read({"share_percent": 0.06}) == Decimal("0.06")
    assert read({"share_percent": "6"}) is None
    assert read({"share_percent": True}) is None
    assert read({}) is oracles.MISSING

    oracles.assert_share_kept({"share_percent": 6}, 6)
    with pytest.raises(AssertionError, match=oracles.SHARE_SIGNATURE):
        oracles.assert_share_kept({"share_percent": 0.06}, 6)
    with pytest.raises(AssertionError, match="reads 7, sent 6") as caught:
        oracles.assert_share_kept({"share_percent": 7}, 6)
    assert oracles.SHARE_DEFECT not in str(caught.value)
    with pytest.raises(AssertionError, match="cannot be read") as caught:
        oracles.assert_share_kept({}, 6)
    assert oracles.SHARE_DEFECT not in str(caught.value)

    def control():
        return True

    rescaled = still_reproduces(
        oracles.SHARE_DEFECT,
        oracles.SHARE_SIGNATURE,
        lambda: oracles.assert_share_kept({"share_percent": 0.025}, 2.5),
        exercised=control,
    )
    assert rescaled is RedVerdict.REPRODUCES
    with pytest.raises(AssertionError, match=FIXED_TAG):
        still_reproduces(
            oracles.SHARE_DEFECT,
            oracles.SHARE_SIGNATURE,
            lambda: oracles.assert_share_kept({"share_percent": 2.5}, 2.5),
            exercised=control,
        )


@pytest.mark.parametrize(
    ("name", "share"), [("reseller-terms-six", 6), ("reseller-terms-two-and-a-half", 2.5)]
)
def test_the_fixtures_send_numeric_shares_and_the_same_flat_fee(name, share):
    sent = json.loads((FIXTURES / f"{name}.json").read_text())
    assert sent["share_percent"] == share and type(sent["share_percent"]) in (int, float)
    assert sent["flat_fee"] == 1.25


def test_expected_commission():
    assert oracles.expected_commission(Decimal("97.50"), Decimal(6), Decimal("1.25")) == Decimal(
        "7.10"
    )
