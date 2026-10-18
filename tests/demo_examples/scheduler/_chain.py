"""Read-only views of the demo's billing schedules, for the registration examples.

Everything here takes a :class:`gate.read_only.ReadOnlyClient`, which has no write verbs;
``test_scheduler_registration.py`` also checks this file's source for write calls.
"""

from __future__ import annotations

from typing import Any

import pytest

from gate.read_only import ReadOnlyClient
from oracles.job_registration import (
    Rule,
    ScheduleRow,
    StatusVocabulary,
    classify_registration,
    parse_rows,
    silently_stalled,
)

#: The demo's transaction states in the oracle's vocabulary.
VOCAB = StatusVocabulary(
    scheduled=frozenset({"scheduled"}),
    pending=frozenset({"initial"}),
    booked=frozenset({"booked"}),
)


def transactions(api: ReadOnlyClient, sub_id: Any) -> list[dict[str, Any]]:
    response = api.get(f"/api/subscriptions/{sub_id}/transactions")
    assert response.status_code == 200, f"transactions of {sub_id}: HTTP {response.status_code}"
    return [t for t in response.json() if t["state"] != "VOID"]


def schedule(api: ReadOnlyClient, sub_id: Any) -> list[ScheduleRow]:
    rows = [
        {**t, "start": f"{t['period_start']}T00:00:00+00:00"} for t in transactions(api, sub_id)
    ]
    return parse_rows(rows, code_key="kind", status_key="state", start_key="start")


def newest_subscription_ids(api: ReadOnlyClient, *, since_id: int = 0, limit: int = 100) -> list:
    """Ids of the newest subscriptions (at most ``limit``), not older than ``since_id``."""
    response = api.get("/api/subscriptions")
    assert response.status_code == 200, f"subscription list: HTTP {response.status_code}"
    ids = sorted((s["id"] for s in response.json() if s["id"] >= since_id), reverse=True)
    return ids[:limit]


def active_faults(api: ReadOnlyClient) -> str:
    """The demo's fault set as it reports it on every response."""
    return api.get("/api/health").headers.get("X-Demo-Faults", "?")


def one_at_a_time_observed(api: ReadOnlyClient, ids: list) -> bool:
    return any(classify_registration(schedule(api, i), VOCAB) is Rule.SINGLE_SCHEDULED for i in ids)


def advanced_once(api: ReadOnlyClient, ids: list) -> bool:
    """Some subscription has a booked transaction with unbooked ones after it."""
    for i in ids:
        rows = schedule(api, i)
        if any(r.status in VOCAB.booked for r in rows) and any(
            r.status in VOCAB.scheduled | VOCAB.pending for r in rows
        ):
            return True
    return False


def silently_stalled_ids(api: ReadOnlyClient, ids: list) -> list[str]:
    out = []
    for i in ids:
        rows = schedule(api, i)
        if silently_stalled(rows, VOCAB):
            out.append(f"{i}: {', '.join(map(str, rows[:3]))}")
    return out


def require_one_at_a_time(rows: list[ScheduleRow]) -> Rule:
    """Abstain (skip) unless the schedule shows the one-at-a-time registration rule."""
    rule = classify_registration(rows, VOCAB)
    if rule is Rule.EVERY_ROW_SCHEDULED:
        pytest.skip("the system registers every transaction at once; one-at-a-time rules abstain")
    if rule is not Rule.SINGLE_SCHEDULED:
        pytest.skip(f"registration rule is {rule.value}; one-at-a-time rules abstain")
    return rule
