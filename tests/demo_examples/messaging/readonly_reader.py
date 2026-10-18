"""Read-only checks over messages since a cut-over. This module must never write.

It takes a :class:`gate.read_only.ReadOnlyClient` (GET only), and
``test_message_formats.py`` asserts with ``find_write_calls`` that no write call appears in
this file, so a later edit cannot quietly turn an inspection into a change.
"""

from __future__ import annotations

from typing import Any

from gate.read_only import ReadOnlyClient
from oracles.ids import time_order_violations, uuid_version
from oracles.send_order import Hypothesis, files_from, orderable, violations


def messages_since(client: ReadOnlyClient, since: str) -> list[dict[str, Any]]:
    """Messages created at or after ``since``, oldest first, with their details."""
    rows = client.get("messages", params={"since": since}).json()
    rows.sort(key=lambda r: (r["created_at"], r["id"]))
    return [client.get(f"messages/{r['id']}").json() for r in rows]


def order_and_id_findings(messages: list[dict[str, Any]], declared: Hypothesis) -> list[str]:
    findings = []
    for m in messages:
        if uuid_version(m["id"]) != 7:
            findings.append(f"{m['id']}: v{uuid_version(m['id'])} id, expected time-ordered v7")
        files = files_from(m)
        if orderable(files):
            findings += [f"{m['id']}: {v}" for v in violations(files, declared)]
    if all(uuid_version(m["id"]) == 7 for m in messages):
        findings += time_order_violations([m["id"] for m in messages])
    return findings
