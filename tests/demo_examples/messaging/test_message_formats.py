"""XML generation after a runtime change, and read-only checks of send order and ids."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import requests

from gate.read_only import ReadOnlyClient, find_write_calls
from oracles.send_order import TYPE_THEN_ID
from tests.demo_examples.messaging import readonly_reader, support


def test_xml_messages_still_generated(demo_sut, demo_seed, outbound, frozen):
    frozen.freeze("2026-03-10T07:00:00Z")
    earlier = support.booked(demo_seed)  # before the runtime change
    frozen.settings(deployed_at="2026-03-10T08:00:00Z")
    deployed_at = demo_sut.client.ok("GET", "/api/_admin/settings")["deployed_at"]
    frozen.freeze(support.NOW)
    later = support.booked(demo_seed)
    for sub in (earlier, later):
        outbound.generate(sub["id"])

    mine = {earlier["id"], later["id"]}
    since = [m for m in outbound.overview(since=deployed_at) if m["subscription_id"] in mine]
    assert [m["subscription_id"] for m in since] == [later["id"]]  # the earlier one predates it
    for message in since:
        root = ET.fromstring(outbound.download(message["id"], fmt="xml"))
        assert root.tag == "documentData" and root.find("subscription") is not None
    (new_one,) = outbound.overview(later["id"])
    assert ET.fromstring(outbound.download(new_one["id"], fmt="xml")).tag == "documentData"


def test_send_order_and_time_ordered_ids(demo_sut, demo_seed, outbound, frozen):
    cutover = "2026-03-10T08:30:00Z"
    frozen.settings(deployed_at=cutover, send_order="v1")
    subs = []
    for _ in range(3):
        subs.append(support.booked(demo_seed))
        demo_sut.client.shift(seconds=2)
    for sub in subs:
        outbound.generate(sub["id"])
        demo_sut.client.ok("POST", "/api/messages/send", {"subscription_id": sub["id"]})

    reader = ReadOnlyClient.over(requests.Session(), demo_sut.api_url)
    messages = [
        m
        for m in readonly_reader.messages_since(reader, cutover)
        if m["subscription_id"] in {s["id"] for s in subs}
    ]
    assert len(messages) == 3 and all(m["status"] == "SENT" for m in messages)
    assert readonly_reader.order_and_id_findings(messages, TYPE_THEN_ID) == []


def test_reader_module_contains_no_write_call():
    assert find_write_calls([Path(readonly_reader.__file__)]) == []
