"""Reseller terms keep the share percent they were given, and settlement applies it."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from test_data.fixture_replay import bind
from tests.demo_examples import detector_pair
from tests.demo_examples.finance import oracles
from tests.demo_examples.finance._support import FROZEN_NOW, START_DATE

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize("fault_on", [True, False], ids=["fault-on", "fault-off"])
@pytest.mark.parametrize("terms_file", ["reseller-terms-six", "reseller-terms-two-and-a-half"])
def test_share_percent_kept(demo_control, demo_seed, demo_sut, terms_file, fault_on):
    demo_control.faults(*(["reseller_share_scaled_twice"] if fault_on else []))
    demo_control.freeze(FROZEN_NOW)
    body = bind(
        json.loads((FIXTURES / f"{terms_file}.json").read_text()),
        {"tag": terms_file},
        lambda name: None,
    )
    created = demo_sut.client.ok("POST", "/api/reseller-terms", body)
    stored = demo_sut.client.ok("GET", f"/api/reseller-terms/{created['id']}")

    partner = demo_seed.partner(reseller_terms_id=created["id"])
    sub = demo_seed.subscription(partner_id=partner["id"], start_date=START_DATE)
    demo_seed.book_due(sub["id"])
    settlement = demo_sut.client.ok("POST", "/api/settlements", {"partner_id": partner["id"]})

    def exercised() -> None:
        # the flat fee rides along on the same terms and never moves: the read is real
        assert stored["flat_fee"] == body["flat_fee"], stored
        assert Decimal(str(settlement["closing_balance"])) > 0, "nothing was settled"

    def oracle() -> None:
        oracles.assert_share_kept(stored, body["share_percent"])
        expected = oracles.expected_commission(
            Decimal(str(sub["price"]["period_net"])),
            Decimal(str(body["share_percent"])),
            Decimal(str(body["flat_fee"])),
        )
        assert Decimal(str(settlement["closing_balance"])) == expected, (
            f"settled {settlement['closing_balance']}, expected {expected}"
        )

    detector_pair.judge(
        demo_control,
        ticket=oracles.SHARE_DEFECT,
        signature=oracles.SHARE_SIGNATURE,
        oracle=oracle,
        exercised=exercised,
        fault="reseller_share_scaled_twice",
        fault_on=fault_on,
    )
