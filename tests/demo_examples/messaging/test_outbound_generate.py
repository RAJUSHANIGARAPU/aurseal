"""Outbound messages client against the demo: overview, generate, files, download, no send."""

from __future__ import annotations

import pytest

from api.clients.outbound_messages_client import NotGenerated
from tests.demo_examples.messaging import support


def test_generate_stores_files_and_sends_nothing(demo_seed, outbound):
    sub = support.booked(demo_seed)
    (message,) = outbound.overview(sub["id"])
    assert (message["type"], message["status"], message["files"]) == ("BOOKING", "NEW", [])
    with pytest.raises(NotGenerated):
        outbound.download(message["id"])

    job = outbound.generate(sub["id"])
    assert job["state"] == "DONE" and job["result"]["generated"] >= 1
    details = outbound.wait_for_files(message["id"])
    assert details["status"] == "TO_BE_SENT"
    assert sorted(f["type"] for f in details["files"]) == ["SEGMENTS", "XML"]
    assert all(f["state"] == "READY" and f["sent_at"] is None for f in details["files"])
    assert outbound.download(message["id"]).startswith(b"HDR+" + message["id"].encode())
    assert outbound.download(message["id"], fmt="xml").startswith(b"<?xml")


def test_overview_is_newest_first(demo_seed, outbound, demo_sut):
    sub = support.booked(demo_seed)
    demo_sut.client.shift(seconds=5)
    draft = demo_seed.draft(sub["id"], "change", changes={"po_reference": "PO-GEN-7"})
    demo_seed.confirm_draft(draft["id"])
    demo_seed.book_due(sub["id"])
    assert [m["type"] for m in outbound.overview(sub["id"])] == ["CHANGE", "BOOKING"]
