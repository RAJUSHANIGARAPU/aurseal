"""A start-date change: captured first, and a refusal attributed before it is judged.

An environment refusal (a draft held elsewhere, another rule's validator) or an unrecognised
one means the change was never exercised, so it can never count as a verdict on the start
date itself; only a refusal that names the start date does.
"""

from __future__ import annotations

import pytest

from tests.demo_examples import detector_pair
from tests.demo_examples.change.oracles import (
    START_CHANGE_DEFECT,
    START_CHANGE_ENVIRONMENT,
    rejection_text,
)
from verification.defect_detector import Attribution, attribute_failure


def capture(seed, sub_id: int, new_start: str) -> dict:
    """Open and confirm the start-date change; report what happened, never raise."""
    opened = seed.client.post(
        f"/api/subscriptions/{sub_id}/drafts",
        {"kind": "change", "changes": {"start_date": new_start}, "effective_date": new_start},
    )
    response = opened
    if 200 <= opened.status < 300:
        response = seed.client.post(f"/api/drafts/{opened.json()['id']}/actions/confirm")
    ok = 200 <= response.status < 300
    text = "" if ok else rejection_text(response.status, response.json())
    attribution = (
        None
        if ok
        else attribute_failure(
            text, defect=START_CHANGE_DEFECT, environment=START_CHANGE_ENVIRONMENT
        )
    )
    return {"ok": ok, "text": text, "attribution": attribution, "view": response.json()}


def test_start_date_change_on_a_booked_subscription_is_not_the_defect(demo_seed, demo_control):
    """Once a period is booked another rule refuses the move: that is the environment."""
    demo_control.freeze("2026-09-14T09:00:00Z")
    sub = demo_seed.subscription(start_date="2026-09-01")
    demo_seed.book_due(sub["id"])
    captured = capture(demo_seed, sub["id"], "2026-09-08")
    assert not captured["ok"]
    assert captured["attribution"] is Attribution.ENVIRONMENT, captured["text"]


def test_an_unbooked_start_date_moves_and_reschedules(demo_seed, demo_control):
    demo_control.freeze("2026-09-14T09:00:00Z")
    sub = demo_seed.subscription(start_date="2026-10-19")
    captured = capture(demo_seed, sub["id"], "2026-10-26")
    assert captured["ok"], captured["text"]
    view = captured["view"]
    assert view["start_date"] == "2026-10-26" and view["term_end"] == "2027-10-26"
    live = [
        t for t in demo_seed.transactions(sub["id"]) if t["state"] != "VOID" and t["kind"] == "NEW"
    ]
    assert live[0]["period_start"] == "2026-10-26"


TICKET = "DEMO-104"
SIGNATURE = rf"{TICKET}: the new start date was not kept"


@pytest.mark.parametrize("fault_on", [True, False], ids=["fault-on", "fault-off"])
def test_start_date_change_keeps_day_and_month_detector(demo_seed, demo_control, fault_on):
    demo_control.faults(*(["effective_date_day_month_swapped"] if fault_on else []))
    demo_control.freeze("2026-01-12T09:00:00Z")
    sub = demo_seed.subscription(start_date="2026-03-20")  # confirmed, nothing booked yet
    captured = capture(demo_seed, sub["id"], "2026-03-04")

    def exercised() -> None:
        if not captured["ok"] and captured["attribution"] is not Attribution.DEFECT:
            pytest.skip(f"not exercised ({captured['attribution'].value}): {captured['text']}")
        # control: a day above 12 cannot be read the other way round
        other = demo_seed.subscription(start_date="2026-03-20")
        late = capture(demo_seed, other["id"], "2026-03-15")
        assert late["ok"] and first_period(demo_seed, other["id"]) == "2026-03-15", late

    def oracle() -> None:
        assert captured["ok"], f"{TICKET}: the change could not be captured: {captured['text']}"
        start = first_period(demo_seed, sub["id"])
        assert start == "2026-03-04", (
            f"{TICKET}: the new start date was not kept: asked 2026-03-04, scheduled from {start}"
        )

    detector_pair.judge(
        demo_control,
        ticket=TICKET,
        signature=SIGNATURE,
        oracle=oracle,
        exercised=exercised,
        fault="effective_date_day_month_swapped",
        fault_on=fault_on,
    )


def first_period(seed, sub_id: int) -> str:
    live = [t for t in seed.transactions(sub_id) if t["state"] != "VOID" and t["kind"] == "NEW"]
    return min(t["period_start"] for t in live)
