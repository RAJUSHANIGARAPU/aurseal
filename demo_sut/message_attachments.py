"""Attachments: extra files on a generated outbound message.

``POST /api/messages/{id}/attachments`` with ``{"name": ..., "content": ...}`` stores one more
file of type ``ATTACHMENT`` on a message that has been generated (``TO_BE_SENT``). A message
can then carry several files of the same type, which is exactly where send orders that break
ties by file name (``send_order`` v2) or by file id (v1) send them differently.

Purely additive: a message without attachments behaves as before.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from demo_sut.core import Conflict, Invalid, NotFound
from demo_sut.http import Request, Response, Router, json_response

if TYPE_CHECKING:
    from demo_sut.app import DemoApp

ATTACHMENT = "ATTACHMENT"


def attach(app: DemoApp, message_id: str, body: dict[str, Any]) -> dict[str, Any]:
    name = str(body.get("name") or "").strip()
    if not name:
        raise Invalid("Attachment name is required.", {"name": "Attachment name is required."})
    message = next(
        (m for m in app.ctx.store.find("messages") if m["message_id"] == message_id), None
    )
    if message is None:
        raise NotFound("message", message_id)
    if message["status"] != "TO_BE_SENT":
        raise Conflict("Only a generated message (TO_BE_SENT) takes attachments.")
    stored = app.docs.store_file(name, "text/plain", str(body.get("content") or "").encode())
    message["files"].append(
        {
            "file_id": stored["id"],
            "type": ATTACHMENT,
            "name": name,
            "state": "READY",
            "sent_at": None,
        }
    )
    app.ctx.store.save("messages", message)
    return app.outbox.public(message)


def register(router: Router) -> None:
    @router.add(
        "POST",
        "/api/messages/{id}/attachments",
        "Add an attachment file to a generated (TO_BE_SENT) message",
        "messages",
        status=201,
        body={
            "type": "object",
            "required": ["name"],
            "properties": {"name": {"type": "string"}, "content": {"type": "string"}},
        },
    )
    def message_attach(app: DemoApp, req: Request, id: str) -> Response:
        return json_response(attach(app, id, req.json()), 201)
