"""Outbound messages: hand-off on booking, generate, content, send order and ids."""

from __future__ import annotations

import time
import uuid
import xml.etree.ElementTree as ET

import pytest

NOW = "2026-03-10T09:00:00Z"


@pytest.fixture
def frozen(demo_control):
    demo_control.freeze(NOW)
    return demo_control


def generate(client, sub_id):
    job = client.ok("POST", "/api/messages/generate", {"subscription_id": sub_id})
    for _ in range(100):
        if client.ok("GET", f"/api/jobs/{job['job_id']}")["state"] == "DONE":
            return
        time.sleep(0.05)
    raise AssertionError("generate job did not finish")


def booked(demo_seed, **fields):
    sub = demo_seed.subscription(start_date="2026-03-01", **fields)
    demo_seed.book_due(sub["id"])
    return sub


def messages(client, sub_id):
    return client.ok("GET", f"/api/messages?subscription_id={sub_id}")


def test_first_booking_stores_exactly_one_message(demo_sut, demo_seed, frozen):
    sub = booked(demo_seed)
    (message,) = messages(demo_sut.client, sub["id"])
    assert message["type"] == "BOOKING" and message["status"] == "NEW"
    demo_sut.client.ok("POST", f"/api/subscriptions/{sub['id']}/invoices/process?until=2026-06-01")
    assert len(messages(demo_sut.client, sub["id"])) == 1  # recurring periods add none
    draft = demo_seed.draft(sub["id"], "change", changes={"po_reference": "PO-MSG-2"})
    demo_seed.confirm_draft(draft["id"])
    demo_seed.book_due(sub["id"])
    found = messages(demo_sut.client, sub["id"])
    assert [m["type"] for m in found] == ["CHANGE", "BOOKING"]  # newest first


def test_content_is_404_while_new_then_segments_after_generate(demo_sut, demo_seed, frozen):
    sub = booked(demo_seed, invoice_notes=[{"text": "Ref on every remittance"}])
    (message,) = messages(demo_sut.client, sub["id"])
    early = demo_sut.client.get(f"/api/messages/{message['id']}/content")
    assert early.status == 404 and early.source == "service"
    generate(demo_sut.client, sub["id"])
    detail = demo_sut.client.ok("GET", f"/api/messages/{message['id']}")
    assert detail["status"] == "TO_BE_SENT"
    assert {f["type"] for f in detail["files"]} == {"SEGMENTS", "XML"}
    body = demo_sut.client.get(f"/api/messages/{message['id']}/content").body.decode("utf-8")
    segments = [s.split("+") for s in body.split("'") if s]
    tags = [s[0] for s in segments]
    assert tags[0] == "HDR" and tags[-1] == "END" and int(segments[-1][1]) == len(segments)
    amounts = {s[1]: float(s[2]) for s in segments if s[0] == "AMT"}
    assert amounts["TOT"] == round(amounts["NET"] + amounts["TAX"] + amounts["FEE"], 2)
    holder = sub["holder"]
    assert ["CUS", holder["number"], "I1", holder["name"]] in segments
    assert ["NTE", "1", "1"] in segments
    xml = demo_sut.client.get(f"/api/messages/{message['id']}/content?format=xml").body
    note = ET.fromstring(xml).find("./invoiceNotes/note")
    assert note.text == "Ref on every remittance" and note.get("number") == "1"


def test_message_ids_are_time_ordered_uuid7(demo_sut, demo_seed, frozen):
    subs = [booked(demo_seed) for _ in range(3)]
    ids = [messages(demo_sut.client, s["id"])[0]["id"] for s in subs]
    parsed = [uuid.UUID(i) for i in ids]
    assert all(u.version == 7 for u in parsed)
    assert ids == sorted(ids)  # same frozen millisecond: the counter keeps them ordered


def test_uuid4_scheme_setting(demo_sut, demo_seed, frozen):
    frozen.settings(message_id_scheme="uuid4")
    sub = booked(demo_seed)
    assert uuid.UUID(messages(demo_sut.client, sub["id"])[0]["id"]).version == 4


@pytest.mark.parametrize("order", ["v1", "v2"])
def test_send_order_setting(demo_sut, demo_seed, frozen, order):
    frozen.settings(send_order=order)
    sub = booked(demo_seed)
    generate(demo_sut.client, sub["id"])
    (sent,) = demo_sut.client.ok("POST", "/api/messages/send", {"subscription_id": sub["id"]})
    assert sent["status"] == "SENT"
    files = sorted(sent["files"], key=lambda f: f["sent_at"])
    key = (
        (lambda f: (f["type"], f["file_id"]))
        if order == "v1"
        else (lambda f: (f["type"], f["name"]))
    )
    assert files == sorted(files, key=key)


def test_since_filter_and_unknown_message(demo_sut, demo_seed, frozen):
    sub = booked(demo_seed)
    later = demo_sut.client.ok("GET", "/api/messages?since=2026-03-10T10:00:00Z")
    assert sub["id"] not in {m["subscription_id"] for m in later}
    at = demo_sut.client.ok("GET", "/api/messages?since=2026-03-10T09:00:00Z")
    assert sub["id"] in {m["subscription_id"] for m in at}
    assert demo_sut.client.get(f"/api/messages/{uuid.uuid4()}").status == 404


@pytest.mark.parametrize("fault_on", [False, True])
def test_non_ascii_send_fault(demo_sut, demo_seed, frozen, fault_on):
    if fault_on:
        frozen.fault("message_send_fails_on_non_ascii")
    plain = booked(demo_seed, customer=demo_seed.customer(display_name="Ola Plain"))
    accented = booked(demo_seed, customer=demo_seed.customer(display_name="Åsa Ørnberg"))
    statuses = {}
    for sub in (plain, accented):
        generate(demo_sut.client, sub["id"])
        (sent,) = demo_sut.client.ok("POST", "/api/messages/send", {"subscription_id": sub["id"]})
        statuses[sub["id"]] = sent["status"]
    assert statuses[plain["id"]] == "SENT"
    assert statuses[accented["id"]] == ("FAILED" if fault_on else "SENT")


@pytest.mark.parametrize("fault_on", [False, True])
def test_xml_double_escape_fault(demo_sut, demo_seed, frozen, fault_on):
    if fault_on:
        frozen.fault("xml_escapes_twice")
    sub = booked(demo_seed, invoice_notes=[{"text": "Ops & Finance <shared>"}])
    generate(demo_sut.client, sub["id"])
    (message,) = messages(demo_sut.client, sub["id"])
    xml = demo_sut.client.get(f"/api/messages/{message['id']}/content?format=xml").body
    text = ET.fromstring(xml).find("./invoiceNotes/note").text
    assert (text == "Ops & Finance <shared>") is not fault_on
