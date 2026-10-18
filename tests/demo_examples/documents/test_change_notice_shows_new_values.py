"""The plan update notice is written from the new version, not the one before (detector pair)."""

from __future__ import annotations

import pytest

from tests.demo_examples import detector_pair
from tests.demo_examples.documents import oracles
from tests.demo_examples.documents._support import (
    CHANGE_EFFECTIVE,
    FROZEN_NOW,
    START_DATE,
    document_texts,
)

TICKET = "DEMO-115"


@pytest.mark.parametrize("fault_on", [True, False], ids=["fault-on", "fault-off"])
def test_change_notice_shows_new_values(demo_control, demo_seed, documents, fault_on):
    demo_control.faults(*(["document_renders_previous_version"] if fault_on else []))
    demo_control.freeze(FROZEN_NOW)
    sub = demo_seed.subscription(
        start_date=START_DATE, data_region="EU-CENTRAL", workspace_type="TEAM_SPACE"
    )
    draft = demo_seed.draft(
        sub["id"],
        "change",
        effective_date=CHANGE_EFFECTIVE,
        changes={"data_region": "EU-NORTH", "workspace_type": "SANDBOX"},
    )
    changed = demo_seed.confirm_draft(draft["id"])
    text = document_texts(documents, sub["id"], ("plan_update_notice",))["plan_update_notice"]

    def exercised() -> None:
        # the subscription number is the same in both versions: the right notice was read
        assert sub["number"] in text, f"{sub['number']} is not on the notice"
        assert changed["version"] == 2

    def oracle() -> None:
        try:
            oracles.assert_line_value(text, "Data region:", "EU-NORTH")
            oracles.assert_line_value(text, "Workspace:", "SANDBOX")
            oracles.assert_line_value(text, "Version:", "2", section=False)
        except AssertionError as failure:
            raise AssertionError(
                f"{TICKET}: the plan update notice does not show the new version ({failure})"
            ) from failure

    detector_pair.judge(
        demo_control,
        ticket=TICKET,
        signature=rf"{TICKET}: the plan update notice does not show the new version",
        oracle=oracle,
        exercised=exercised,
        fault="document_renders_previous_version",
        fault_on=fault_on,
    )
