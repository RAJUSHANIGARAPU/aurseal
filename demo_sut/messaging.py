"""Outbound partner messages.

Booking the first transaction of a create, change, renewal or cancellation hands exactly
one message to the outbox (status ``NEW``). *Generate* (a polled background job) turns NEW
messages into ``TO_BE_SENT`` and stores two files each: a segment payload and an XML
document-data file. *Send* delivers TO_BE_SENT messages (``SENT`` or ``FAILED``) in the
configured file order. Nothing ever leaves the process.

Segment syntax (invented): segments end with ``'``, elements are separated by ``+``::

    HDR+<message id>+<type>'SUB+<number>+<version>'CUS+<customer number>+<type code>+<name>'
    AMT+NET+<net>'AMT+TAX+<tax>'AMT+FEE+<fee>'AMT+TOT+<total>'NTE+<n>+<version>'END+<count>'

Text is UTF-8 throughout; a customer name is carried as it was entered.
"""

from __future__ import annotations

import secrets
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from decimal import Decimal
from typing import Any
from xml.sax.saxutils import escape

from demo_sut.clock import normalize_instant
from demo_sut.core import Context, NotFound, num
from demo_sut.customers import TYPE_CODES
from demo_sut.documents import DocumentRenderer

MESSAGE_TYPES = {
    "NEW": "BOOKING",
    "CHANGE": "CHANGE",
    "RENEW": "RENEWAL",
    "CANCEL": "CANCELLATION",
}


def new_message_id(ctx: Context) -> str:
    """A message id: UUIDv7 (time-ordered, strictly increasing) or UUIDv4 per the setting."""
    if ctx.settings["message_id_scheme"] == "uuid4":
        return str(uuid.uuid4())
    ms = int(ctx.clock.now().timestamp() * 1000)
    last_ms = ctx.counters.get("uuid7_ms", -1)
    seq = 0
    if ms <= last_ms:
        ms, seq = last_ms, ctx.counters.get("uuid7_seq", 0) + 1
        if seq > 0xFFF:
            ms, seq = ms + 1, 0
    ctx.counters["uuid7_ms"], ctx.counters["uuid7_seq"] = ms, seq
    value = (ms & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76
    value |= seq << 64
    value |= 0b10 << 62
    value |= secrets.randbits(62)
    return str(uuid.UUID(int=value))


def render_segments(
    message: dict[str, Any], sub: dict[str, Any], tx: dict[str, Any], customer: dict[str, Any]
) -> str:
    net = Decimal(str(tx["net"]))
    tax = Decimal(str(tx["tax"])) + Decimal(str(tx["fee_tax"]))
    fee = Decimal(str(tx["fee_net"]))
    segments = [
        f"HDR+{message['message_id']}+{message['type']}",
        f"SUB+{sub['number']}+{tx['version']}",
        f"CUS+{customer.get('number', '')}+{TYPE_CODES.get(customer.get('type', ''), 'XX')}"
        f"+{customer.get('display_name', '')}",
        f"AMT+NET+{num(net):.2f}",
        f"AMT+TAX+{num(tax):.2f}",
        f"AMT+FEE+{num(fee):.2f}",
        f"AMT+TOT+{num(net + tax + fee):.2f}",
    ]
    segments += [f"NTE+{n['number']}+{n['version']}" for n in sub.get("invoice_notes") or []]
    segments.append(f"END+{len(segments) + 1}")
    return "".join(s + "'" for s in segments)


def render_xml(sub: dict[str, Any], *, escape_twice: bool = False) -> str:
    root = ET.Element("documentData")
    ET.SubElement(root, "subscription", number=sub["number"], version=str(sub["version"]))
    notes = ET.SubElement(root, "invoiceNotes")
    for n in sub.get("invoice_notes") or []:
        node = ET.SubElement(notes, "note", number=str(n["number"]), version=str(n["version"]))
        node.text = escape(n["text"]) if escape_twice else n["text"]
    return '<?xml version="1.0" encoding="UTF-8"?>' + ET.tostring(root, encoding="unicode")


class Outbox:
    def __init__(self, ctx: Context, docs: DocumentRenderer) -> None:
        self.ctx = ctx
        self.docs = docs

    # -- hand-off --------------------------------------------------------------

    def on_booked(self, tx: dict[str, Any], invoice: dict[str, Any]) -> None:
        origin = tx.get("origin") or f"draft:{tx.get('draft_id')}"
        existing = self.ctx.store.find("messages", subscription_id=tx["subscription_id"])
        if any(m["origin"] == origin for m in existing):
            return
        self._store(tx, origin)

    def _store(self, tx: dict[str, Any], origin: str) -> dict[str, Any]:
        sub = self.ctx.store.get("subscriptions", tx["subscription_id"]) or {}
        return self.ctx.store.insert(
            "messages",
            {
                "message_id": new_message_id(self.ctx),
                "subscription_id": tx["subscription_id"],
                "subscription_number": sub.get("number"),
                "type": MESSAGE_TYPES[tx["kind"]],
                "status": "NEW",
                "recipient": (sub.get("partner") or {}).get("number"),
                "origin": origin,
                "transaction_id": tx["id"],
                "created_at": self.ctx.stamp(),
                "files": [],
            },
        )

    # -- reads -----------------------------------------------------------------

    @staticmethod
    def public(message: dict[str, Any]) -> dict[str, Any]:
        out = {k: v for k, v in message.items() if k not in ("id", "message_id")}
        return {"id": message["message_id"], **out}

    def list(self, subscription_id: Any = None, since: str | None = None) -> list[dict]:
        rows = self.ctx.store.find("messages")
        if subscription_id not in (None, ""):
            rows = [m for m in rows if str(m["subscription_id"]) == str(subscription_id)]
        if since:
            since = normalize_instant(since)
            rows = [m for m in rows if m["created_at"] >= since]
        rows.sort(key=lambda m: (m["created_at"], m["id"]), reverse=True)
        return [self.public(m) for m in rows]

    def _raw(self, message_id: str) -> dict[str, Any]:
        for m in self.ctx.store.find("messages"):
            if m["message_id"] == message_id:
                return m
        raise NotFound("message", message_id)

    def get(self, message_id: str) -> dict[str, Any]:
        return self.public(self._raw(message_id))

    def content(self, message_id: str, kind: str = "SEGMENTS") -> tuple[dict, bytes]:
        message = self._raw(message_id)
        for f in message["files"]:
            if f["type"] == kind:
                return self.docs.file(f["file_id"])
        raise NotFound("message content", message_id)

    # -- generate (background) -------------------------------------------------

    def generate_now(self, subscription_id: Any = None) -> int:
        count = 0
        for m in self.ctx.store.find("messages", status="NEW"):
            if subscription_id not in (None, "") and str(m["subscription_id"]) != str(
                subscription_id
            ):
                continue
            sub = self.ctx.store.get("subscriptions", m["subscription_id"]) or {}
            tx = self.ctx.store.get("transactions", m["transaction_id"]) or {}
            customer = self.ctx.store.get("customers", sub.get("customer_id")) or {}
            seg = render_segments(m, sub, tx, customer).encode("utf-8")
            xml = render_xml(sub, escape_twice=self.ctx.fault("xml_escapes_twice")).encode("utf-8")
            files = []
            for kind, body, ext, ctype in (
                ("SEGMENTS", seg, "txt", "text/plain"),
                ("XML", xml, "xml", "application/xml"),
            ):
                name = f"{m['subscription_number']}-{m['type'].lower()}-{m['id']}.{ext}"
                stored = self.docs.store_file(name, ctype, body)
                files.append(
                    {
                        "file_id": stored["id"],
                        "type": kind,
                        "name": name,
                        "state": "READY",
                        "sent_at": None,
                    }
                )
            m.update(status="TO_BE_SENT", files=files, generated_at=self.ctx.stamp())
            self.ctx.store.save("messages", m)
            count += 1
        return count

    def generate(self, subscription_id: Any = None) -> dict[str, Any]:
        job = self.ctx.store.insert(
            "jobs",
            {
                "kind": "GENERATE_MESSAGES",
                "state": "RUNNING",
                "due_date": self.ctx.clock.today().isoformat(),
                "subscription_id": subscription_id,
                "created_at": self.ctx.stamp(),
                "result": None,
            },
        )
        delay = self.ctx.settings["background_delay_ms"] / 1000

        def work() -> None:
            time.sleep(delay)
            with self.ctx.store.lock:
                current = self.ctx.store.get("jobs", job["id"])
                if current is None:  # the database was reset meanwhile
                    return
                count = self.generate_now(subscription_id)
                current.update(
                    state="DONE", result={"generated": count}, finished_at=self.ctx.stamp()
                )
                self.ctx.store.save("jobs", current)

        thread = threading.Thread(target=work, name=f"demo-job-{job['id']}", daemon=True)
        self.ctx.background.append(thread)
        thread.start()
        return job

    # -- send ------------------------------------------------------------------

    def send(self, subscription_id: Any = None) -> list[dict[str, Any]]:
        out = []
        order = self.ctx.settings["send_order"]
        stamp_ms = int(self.ctx.clock.now().timestamp() * 1000)
        for m in self.ctx.store.find("messages", status="TO_BE_SENT"):
            if subscription_id not in (None, "") and str(m["subscription_id"]) != str(
                subscription_id
            ):
                continue
            key = (
                (lambda f: (f["type"], f["file_id"]))
                if order == "v1"
                else (lambda f: (f["type"], f["name"]))
            )
            failure = self._write_attachments(m)
            if failure:
                m.update(status="FAILED", error=failure)
            else:
                for f in sorted(m["files"], key=key):
                    stamp_ms += 1
                    f.update(state="SENT", sent_at=_ms_iso(stamp_ms))
                m.update(status="SENT", sent_at=_ms_iso(stamp_ms))
            self.ctx.store.save("messages", m)
            out.append(self.public(m))
        return out

    def _write_attachments(self, message: dict[str, Any]) -> str:
        """Hand the text attachment to the (invented) carrier; '' or why it failed."""
        for f in message["files"]:
            if f["type"] != "SEGMENTS":
                continue
            _, body = self.docs.file(f["file_id"])
            text = body.decode("utf-8")
            codec = "ascii" if self.ctx.fault("message_send_fails_on_non_ascii") else "utf-8"
            try:
                text.encode(codec)
            except UnicodeEncodeError as exc:
                return f"attachment could not be written: {exc.reason}"
        return ""


def _ms_iso(ms: int) -> str:
    from datetime import datetime, timezone

    moment = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")
