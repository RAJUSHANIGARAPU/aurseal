"""Seeding and reading helpers shared by the demo UI examples (API side, no browser)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date
from decimal import Decimal
from typing import Any

NOW = "2026-03-10T09:00:00Z"

#: company = a company holder on the team plan; individual = a person on the individual plan
SEGMENTS = {
    "company": {"type": "COMPANY", "plan": "TEAM"},
    "individual": {"type": "INDIVIDUAL", "plan": "INDIVIDUAL"},
}


def segment_customer(seed, segment: str, **fields: Any) -> dict[str, Any]:
    return seed.customer(type=SEGMENTS[segment]["type"], **fields)


def segment_subscription(seed, segment: str, **fields: Any) -> dict[str, Any]:
    customer = fields.pop("customer", None) or segment_customer(seed, segment)
    return seed.subscription(customer=customer, plan_kind=SEGMENTS[segment]["plan"], **fields)


def booked(seed, segment: str = "company", start_date: str = "2026-03-01", **fields: Any):
    """An active subscription whose first period is booked (the clock must be frozen)."""
    sub = segment_subscription(seed, segment, start_date=start_date, **fields)
    assert seed.book_due(sub["id"]), "the first period was not booked; is the clock frozen?"
    return sub


def transactions(client, sub_id: int, kind: str | None = None) -> list[dict[str, Any]]:
    rows = client.ok("GET", f"/api/subscriptions/{sub_id}/transactions")
    return [r for r in rows if kind is None or r["kind"] == kind]


def subscription(client, sub_id: int, version: int | None = None) -> dict[str, Any]:
    query = f"?version={version}" if version is not None else ""
    return client.ok("GET", f"/api/subscriptions/{sub_id}{query}")


def latest(client, sub_id: int) -> dict[str, Any]:
    """The newest version, including one that takes effect after today."""
    newest = subscription(client, sub_id)["latest_version"]
    return subscription(client, sub_id, version=newest)


def money(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))


def first_complete(
    candidates: Iterable[tuple[str, Mapping[str, Any] | None]],
    keys: tuple[str, ...] = ("account", "holder"),
) -> tuple[str, dict[str, Any]] | None:
    """
    The first candidate whose every ``key`` is present and non-blank.

    A half-filled candidate (an account without a holder) is skipped, not used:
    paying a credit to a pair nobody could verify is worse than taking the next
    complete source.
    """
    for source, values in candidates:
        if values and all(str(values.get(k) or "").strip() for k in keys):
            return source, {k: values[k] for k in keys}
    return None


def date_of(text: str) -> date:
    return date.fromisoformat(text)


def reusable(client, *, not_after: str = "2026-03-16") -> dict[str, Any] | None:
    """
    An existing active subscription with a booked period around ``not_after``.

    Newest first; ``None`` when the pool is empty (the caller seeds instead).
    """
    items: list[dict[str, Any]] = []
    cursor = None
    while True:
        body: dict[str, Any] = {"resource": "subscriptions", "limit": 100}
        if cursor:
            body["after"] = cursor
        page = client.ok("POST", "/api/search", body)
        items.extend(page["items"])
        cursor = page["next_cursor"]
        if not cursor:
            break
    for item in reversed(items):
        if item["status"] != "active" or item.get("open_draft_id"):
            continue
        if item["start_date"] > not_after:
            continue
        booked_rows = [
            t
            for t in transactions(client, item["id"])
            if t["state"] == "BOOKED" and t["period_start"] <= not_after < t["period_end"]
        ]
        if booked_rows:
            return item
    return None
