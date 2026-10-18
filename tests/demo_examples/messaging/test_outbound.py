"""Outbound messages end to end: bookings and changes reach the outbox, complete and once,
and a customer name outside ASCII goes out intact."""

from __future__ import annotations

import pytest

from api.clients.outbound_messages_client import NotGenerated
from oracles.message_ledger import stored_once
from oracles.message_rules import evaluate, violations
from tests.demo_examples import detector_pair
from tests.demo_examples.messaging import support
from tests.demo_examples.messaging.rules import demo_rules


def test_change_stores_exactly_one_message(demo_sut, demo_seed, outbound):
    sub = support.booked(demo_seed)
    before = outbound.overview(sub["id"])
    # control: the booking stored one message, and it is complete
    assert [m["type"] for m in before] == ["BOOKING"]
    outbound.generate(sub["id"])
    booking = support.payload(outbound, before[0]["id"])
    assert violations(evaluate(booking, demo_rules("INDIVIDUAL"))) == []

    demo_sut.client.shift(seconds=1)
    draft = demo_seed.draft(
        sub["id"], "change", effective_date=support.NOW[:10], changes={"po_reference": "PO-OUT-31"}
    )
    demo_seed.confirm_draft(draft["id"])
    demo_seed.book_due(sub["id"])
    after = outbound.overview(sub["id"])
    assert stored_once(before, after, handoff_succeeded=True, expected_type="CHANGE") == []
    (change,) = [m for m in after if m["type"] == "CHANGE"]
    outbound.generate(sub["id"])
    assert (
        violations(evaluate(support.payload(outbound, change["id"]), demo_rules("INDIVIDUAL")))
        == []
    )


def test_outbound_segment_message_complete(demo_seed, outbound):
    sub = support.booked(demo_seed, invoice_notes=[{"text": "Remit within the month"}])
    (message,) = outbound.overview(sub["id"])
    details = support.generate_until_files(outbound, sub["id"], message["id"])
    assert details["status"] == "TO_BE_SENT"
    parsed = support.payload(outbound, message["id"])
    assert [s.tag for s in parsed.segments].count("NTE") == 1
    assert violations(evaluate(parsed, demo_rules("INDIVIDUAL"))) == []


def wait_listed(outbound, sub_id, *, attempts=20):
    for _ in range(attempts):
        found = outbound.overview(sub_id)
        if found:
            return found
    raise AssertionError(f"no message for subscription {sub_id} after {attempts} reads")


def test_booked_and_changed_subscriptions_reach_outbox(demo_sut, demo_seed, outbound):
    subs = {kind: support.booked(demo_seed, plan_kind=kind) for kind in ("TEAM", "INDIVIDUAL")}
    for kind, sub in subs.items():
        (message,) = wait_listed(outbound, sub["id"])
        assert message["type"] == "BOOKING", kind
        try:
            outbound.download(message["id"])
        except NotGenerated:
            # a missing file is acceptable only while the message is still NEW
            assert outbound.details(message["id"])["status"] == "NEW", kind
    team = subs["TEAM"]
    draft = demo_seed.draft(team["id"], "change", changes={"po_reference": "PO-OUT-32"})
    demo_seed.confirm_draft(draft["id"])
    demo_seed.book_due(team["id"])
    assert "CHANGE" in [m["type"] for m in wait_listed(outbound, team["id"])]


# -- a name with letters outside ASCII goes out intact -----------------------------------------

NON_ASCII_NAME = "Zoë Ångström"


def send_one(demo_sut, outbound, sub) -> dict:
    outbound.generate(sub["id"])
    (sent,) = demo_sut.client.ok("POST", "/api/messages/send", {"subscription_id": sub["id"]})
    return sent


def assert_sent_intact(sent: dict, raw: bytes, name: str) -> None:
    assert sent["status"] == "SENT", f"the send FAILED for {name!r}: {sent.get('error')}"
    assert name.encode("utf-8") in raw, f"{name!r} is not carried byte for byte (UTF-8)"


def test_non_ascii_name_is_sent_intact(demo_sut, demo_seed, outbound):
    customer = demo_seed.customer(display_name=NON_ASCII_NAME)
    sub = support.booked(demo_seed, customer=customer)
    sent = send_one(demo_sut, outbound, sub)
    raw = outbound.download(sent["id"])
    assert_sent_intact(sent, raw, NON_ASCII_NAME)


@pytest.mark.parametrize("fault_on", [True, False], ids=["fault-on", "fault-off"])
def test_encoding_guard_bites_when_the_defect_is_back(
    demo_sut, demo_seed, demo_control, outbound, fault_on
):
    demo_control.faults(*(["message_send_fails_on_non_ascii"] if fault_on else []))
    accented = support.booked(demo_seed, customer=demo_seed.customer(display_name=NON_ASCII_NAME))
    sent = send_one(demo_sut, outbound, accented)

    def exercised() -> None:
        plain = support.booked(demo_seed, customer=demo_seed.customer(display_name="Ivo Plainname"))
        assert send_one(demo_sut, outbound, plain)["status"] == "SENT", "control: ASCII name sent"

    def oracle() -> None:
        raw = outbound.download(sent["id"])
        assert sent["status"] == "SENT", (
            f"DEMO-108: the send FAILED for a non-ASCII name: {sent.get('error')}"
        )
        assert_sent_intact(sent, raw, NON_ASCII_NAME)

    detector_pair.judge(
        demo_control,
        ticket="DEMO-108",
        signature=r"DEMO-108: the send FAILED for a non-ASCII name",
        oracle=oracle,
        exercised=exercised,
        fault="message_send_fails_on_non_ascii",
        fault_on=fault_on,
    )
