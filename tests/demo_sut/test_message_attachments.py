"""Attachments on generated messages, and how the two send orders treat them."""

from __future__ import annotations

import time

import pytest

NOW = "2026-03-10T09:00:00Z"


def generated_message(client, demo_seed):
    sub = demo_seed.subscription(start_date="2026-03-01")
    demo_seed.book_due(sub["id"])
    job = client.ok("POST", "/api/messages/generate", {"subscription_id": sub["id"]})
    for _ in range(100):
        if client.ok("GET", f"/api/jobs/{job['job_id']}")["state"] == "DONE":
            break
        time.sleep(0.05)
    (message,) = client.ok("GET", f"/api/messages?subscription_id={sub['id']}")
    return sub, message


def test_attachment_needs_a_generated_message_and_a_name(demo_sut, demo_seed, demo_control):
    demo_control.freeze(NOW)
    client = demo_sut.client
    sub = demo_seed.subscription(start_date="2026-03-01")
    demo_seed.book_due(sub["id"])
    (new,) = client.ok("GET", f"/api/messages?subscription_id={sub['id']}")
    assert client.post(f"/api/messages/{new['id']}/attachments", {"name": "a"}).status == 409
    assert client.post("/api/messages/nope/attachments", {"name": "a"}).status == 404
    _, message = generated_message(client, demo_seed)
    assert client.post(f"/api/messages/{message['id']}/attachments", {}).status == 422


@pytest.mark.parametrize(
    ("order", "sent"), [("v1", ["b.txt", "a.txt"]), ("v2", ["a.txt", "b.txt"])]
)
def test_attachments_are_sent_by_id_or_by_name(demo_sut, demo_seed, demo_control, order, sent):
    demo_control.freeze(NOW)
    demo_control.settings(send_order=order)
    client = demo_sut.client
    sub, message = generated_message(client, demo_seed)
    for name in ("b.txt", "a.txt"):  # stored in this order, so ids and names disagree
        added = client.ok("POST", f"/api/messages/{message['id']}/attachments", {"name": name})
    assert [f["type"] for f in added["files"]].count("ATTACHMENT") == 2
    (result,) = client.ok("POST", "/api/messages/send", {"subscription_id": sub["id"]})
    files = sorted(result["files"], key=lambda f: f["sent_at"])
    assert [f["name"] for f in files if f["type"] == "ATTACHMENT"] == sent
