"""The generic rule runner over the demo's outbound partner messages."""

from __future__ import annotations

import pytest

from oracles.message_rules import evaluate, violations
from tests.demo_examples.messaging import support
from tests.demo_examples.messaging.rules import demo_rules


@pytest.mark.parametrize("customer_type", ["INDIVIDUAL", "COMPANY"])
def test_outbound_payload_holds_every_rule(demo_seed, outbound, customer_type):
    customer = demo_seed.customer(type=customer_type, display_name=f"Rules {customer_type.title()}")
    sub = support.booked(
        demo_seed, customer=customer, invoice_notes=[{"text": "Remit within the month"}]
    )
    (message,) = outbound.overview(sub["id"])
    outbound.generate(sub["id"])
    results = evaluate(support.payload(outbound, message["id"]), demo_rules(customer_type))
    assert violations(results) == []
    assert len(results) == 7


def test_rules_catch_a_tampered_payload(demo_seed, outbound):
    sub = support.booked(demo_seed)
    (message,) = outbound.overview(sub["id"])
    outbound.generate(sub["id"])
    good = support.payload(outbound, message["id"])
    tot = next(s for s in good.segments if s.tag == "AMT" and s.element(0) == "TOT")
    tampered = support.OutboundPayload(
        good.message_id,
        [s for s in good.segments if s.tag != "CUS"],
        good.xml.replace("</documentData>", ""),
        good.raw,
    )
    found = violations(evaluate(tampered, demo_rules("INDIVIDUAL")))
    assert any(v.startswith("customer: 0 CUS segment(s)") for v in found)
    assert any(v.startswith("segment count: declares") for v in found)
    assert any(v.startswith("document data: XML part is not well-formed") for v in found)
    assert tot.element(1)  # the total itself was read from the real message
