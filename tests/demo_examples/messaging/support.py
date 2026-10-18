"""Shared helpers for the messaging examples: the frozen clock, booking, payload parsing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from api.clients.outbound_messages_client import OutboundMessagesClient
from oracles.edifact import Segment, parse_interchange

NOW = "2026-03-10T09:00:00Z"
START = "2026-03-01"


def booked(demo_seed: Any, **fields: Any) -> dict[str, Any]:
    """An active subscription whose first period is booked, so its message is handed off."""
    sub = demo_seed.subscription(start_date=fields.pop("start_date", START), **fields)
    demo_seed.book_due(sub["id"])
    return sub


@dataclass(frozen=True)
class OutboundPayload:
    """One generated message: its segments and its XML document-data part."""

    message_id: str
    segments: list[Segment]
    xml: str
    raw: str


def payload(outbound: OutboundMessagesClient, message_id: str) -> OutboundPayload:
    raw = outbound.download(message_id).decode()
    xml = outbound.download(message_id, fmt="xml").decode()
    return OutboundPayload(message_id, parse_interchange(raw).segments, xml, raw)


def generate_until_files(
    outbound: OutboundMessagesClient, subscription_id: Any, message_id: str, *, attempts: int = 3
) -> dict[str, Any]:
    """Generate, and generate again while the message is still NEW (bounded)."""
    details: dict[str, Any] = {}
    for _ in range(attempts):
        outbound.generate(subscription_id)
        details = outbound.details(message_id)
        if details["status"] != "NEW":
            return details
    raise AssertionError(
        f"message {message_id} still NEW after {attempts} generate runs: {details}"
    )
