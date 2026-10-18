"""Shared steps for the change examples: an active subscription, a change through a draft,
the reuse pool and a small path reader for expected values.

Everything goes through the demo's public API.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from test_data.pool import ReusePool, Target, select_targets
from test_data.seed_mode import resolve


def active_subscription(
    seed: Any,
    control: Any,
    *,
    now: str,
    start: str,
    plan_kind: str = "TEAM",
    book: bool = True,
    **fields: Any,
) -> dict[str, Any]:
    """An active subscription; with ``book`` every period up to ``now`` is booked."""
    control.freeze(f"{now}T09:00:00Z")
    sub = seed.subscription(start_date=start, plan_kind=plan_kind, **fields)
    if book:
        seed.book_due(sub["id"])
    return seed.client.ok("GET", f"/api/subscriptions/{sub['id']}")


def open_change(seed: Any, sub_id: int, changes: dict[str, Any], **fields: Any) -> dict[str, Any]:
    return seed.draft(sub_id, "change", changes=changes, **fields)


def finish_change(seed: Any, draft: dict[str, Any]) -> dict[str, Any]:
    """Confirm the change (an ownership transfer must have been accepted first)."""
    return seed.confirm_draft(draft["id"])


def change(seed: Any, sub_id: int, changes: dict[str, Any], **fields: Any) -> dict[str, Any]:
    """Open a change draft with ``changes``, finish it, return the new version."""
    return finish_change(seed, open_change(seed, sub_id, changes, **fields))


def current(seed: Any, sub_id: int, version: int | None = None) -> dict[str, Any]:
    suffix = f"?version={version}" if version is not None else ""
    return seed.client.ok("GET", f"/api/subscriptions/{sub_id}{suffix}")


def change_transactions(seed: Any, sub_id: int) -> list[dict[str, Any]]:
    return [t for t in seed.transactions(sub_id) if t["kind"] == "CHANGE"]


def read_path(doc: Any, path: str) -> Any:
    """``"price.monthly_net"``, ``"labels.-1.code"``: dict keys and list indexes."""
    node = doc
    for part in path.split("."):
        if isinstance(node, list):
            try:
                node = node[int(part)]
            except (IndexError, ValueError):
                return None
        elif isinstance(node, dict):
            node = node.get(part)
        else:
            return None
    return node


def reuse_targets(
    seed: Any,
    control: Any,
    *,
    now: str,
    fit: Callable[[dict[str, Any]], bool],
    build: Callable[[], dict[str, Any]],
    limit: int = 3,
    scan: int = 40,
) -> list[Target[dict[str, Any]]]:
    """Borrow fitting active subscriptions first (reuse mode); build one when none fits."""
    control.freeze(f"{now}T09:00:00Z")

    def candidates() -> list[dict[str, Any]]:
        rows = seed.client.ok("GET", "/api/subscriptions?status=active")
        rows = [r for r in rows if not r.get("open_draft_id")]
        return sorted(rows, key=lambda s: s["id"], reverse=True)[:scan]

    pool = ReusePool(candidates, fit=fit, label=lambda s: s["number"])
    return select_targets(resolve({"SEED_MODE": "reuse"}), pool, build, limit=limit)
