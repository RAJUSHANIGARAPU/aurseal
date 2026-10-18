"""Shared steps for the closure examples: a processed subscription, its paid periods, a
closure through a draft, and the reuse pool.

Everything goes through the demo's public API. The oracles live in :mod:`oracles`; this
module only arranges data and reads it back.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from oracles.amounts import parse_json_amount
from oracles.daycount import Period, assert_pro_rata_refund
from oracles.period_window import Window, last_booked_period
from test_data.pool import ReusePool, Target, select_targets
from test_data.seed_mode import resolve

PRICE_KINDS = ("NEW", "RENEW")


def processed_subscription(
    seed: Any, control: Any, *, now: str, start: str, plan_kind: str = "TEAM", **fields: Any
) -> dict[str, Any]:
    """An active subscription whose periods up to ``now`` are booked (the 'processed' state)."""
    control.freeze(f"{now}T09:00:00Z")
    sub = seed.subscription(start_date=start, plan_kind=plan_kind, **fields)
    seed.book_due(sub["id"])
    return sub


def paid_periods(seed: Any, sub_id: int) -> list[dict[str, Any]]:
    """Booked price transactions (the periods the customer has paid for), oldest first."""
    return [
        t for t in seed.transactions(sub_id) if t["state"] == "BOOKED" and t["kind"] in PRICE_KINDS
    ]


def window_of(tx: dict[str, Any]) -> Window:
    """The transaction's period with an inclusive end (the demo stores the next start)."""
    return Window(
        date.fromisoformat(tx["period_start"]),
        date.fromisoformat(tx["period_end"]) - timedelta(days=1),
    )


def last_paid(seed: Any, sub_id: int) -> tuple[dict[str, Any], Window]:
    periods = paid_periods(seed, sub_id)
    assert periods, f"subscription {sub_id} has no booked period: it was never processed"
    rows = [(*_bounds(t), t["state"]) for t in periods]
    window = last_booked_period(rows)
    assert window is not None
    tx = next(t for t in periods if window_of(t) == window)
    return tx, window


def _bounds(tx: dict[str, Any]) -> tuple[date, date]:
    w = window_of(tx)
    return w.start, w.end


def cancel(
    seed: Any, sub_id: int, effective: date | str, reason: str = "NO_LONGER_NEEDED"
) -> tuple[dict[str, Any], dict[str, Any]]:
    """End the subscription through a draft; returns the new version and its CANCEL row."""
    draft = seed.draft(sub_id, "cancel", effective_date=str(effective), reason=reason)
    view = seed.confirm_draft(draft["id"])
    return view, cancel_transaction(seed, sub_id)


def cancel_transaction(seed: Any, sub_id: int) -> dict[str, Any]:
    rows = [t for t in seed.transactions(sub_id) if t["kind"] == "CANCEL"]
    assert len(rows) == 1, f"expected exactly one CANCEL transaction, found {len(rows)}"
    return rows[0]


def oracle_period(tx: dict[str, Any], termination: date) -> Period:
    return Period(
        date.fromisoformat(tx["period_start"]),
        date.fromisoformat(tx["period_end"]),
        termination,
        end_inclusive=False,
    )


def amount(value: Any) -> Decimal:
    parsed = parse_json_amount(value)
    assert parsed is not None, f"amount missing: {value!r}"
    return parsed


def assert_refund_within_bounds(
    context: str, paid: dict[str, Any], termination: date, view: dict[str, Any]
) -> None:
    """The convention-independent oracle over the credit the demo recorded."""
    closure = view["closure"]
    assert_pro_rata_refund(
        context,
        oracle_period(paid, termination),
        amount(paid["net"]),
        amount(closure["credit_net"]),
        refund_tax=amount(closure["credit_tax"]),
        tax_rate=amount(view["price"]["tax_rate"]) / 100,
    )


# -- reuse pool -----------------------------------------------------------------------------


def fits_for_cancel(seed: Any, sub: dict[str, Any], chosen: date, plan_kind: str) -> bool:
    """Active, no open draft, paid for exactly up to a period that contains ``chosen``,
    only price transactions so far, and an account to pay a credit to."""
    if sub["status"] not in ("active", "renewed") or sub.get("open_draft_id"):
        return False
    if sub.get("plan_kind") != plan_kind:
        return False
    live = [t for t in seed.transactions(sub["id"]) if t["state"] != "VOID"]
    if any(t["kind"] not in PRICE_KINDS for t in live):
        return False
    booked = [t for t in live if t["state"] == "BOOKED"]
    containing = [t for t in booked if window_of(t).start < chosen <= window_of(t).end]
    later = [t for t in booked if window_of(t).start >= chosen]
    if len(containing) != 1 or later:
        return False
    if (sub.get("payment_method") or {}).get("account"):
        return True
    customer = seed.client.ok("GET", f"/api/customers/{sub['customer_id']}")
    return bool(customer.get("payout_account"))


def reuse_targets(
    seed: Any,
    control: Any,
    *,
    now: str,
    chosen: date,
    plan_kind: str = "TEAM",
    limit: int = 3,
    scan: int = 40,
) -> list[Target[dict[str, Any]]]:
    """Borrow fitting active subscriptions first; build a processed one when none fits."""
    control.freeze(f"{now}T09:00:00Z")

    def candidates() -> list[dict[str, Any]]:
        rows = seed.client.ok("GET", "/api/subscriptions?status=active")
        return sorted(rows, key=lambda s: s["id"], reverse=True)[:scan]

    month_start = date.fromisoformat(now).replace(day=1)
    start = (month_start - timedelta(days=40)).replace(day=1)

    def build() -> dict[str, Any]:
        return processed_subscription(
            seed, control, now=now, start=start.isoformat(), plan_kind=plan_kind
        )

    pool = ReusePool(
        candidates,
        fit=lambda s: fits_for_cancel(seed, s, chosen, plan_kind),
        label=lambda s: s["number"],
    )
    return select_targets(resolve({"SEED_MODE": "reuse"}), pool, build, limit=limit)
