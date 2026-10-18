"""Drive a billing schedule synchronously, without waiting for the background scheduler.

Set-up that needs booked periods (an invoice to pay, a renewal that requires every period
before the term end booked) should not wait for a scheduler that fires on period start
dates. Most billing services expose a synchronous trigger next to the scheduler; this
client wraps it and the scheduler's own route over :class:`~api.clients.http_engine.HttpEngine`::

    billing = BillingRunner(engine)
    first = billing.process_next_due(sub_id)          # book the next scheduled period now
    rest = billing.process_all_due(sub_id)            # ... and every one after it
    billing.run_due(until="2026-05-01")               # the scheduler's own route
    tx = billing.await_transaction(sub_id, kind="CANCEL", states={"SCHEDULED"})

Paths, field names and the "nothing left" statuses are constructor arguments, so the same
client fits another service. Every non-2xx answer other than "nothing left" raises
:class:`BillingError` quoting the response; nothing is retried or swallowed.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from api.clients.http_engine import ApiResponse, HttpEngine


class BillingError(AssertionError):
    """A billing call was refused; the message carries the response."""

    def __init__(self, what: str, response: ApiResponse) -> None:
        super().__init__(f"{what} failed: {response}")
        self.response = response


class BillingRunner:
    def __init__(
        self,
        engine: HttpEngine,
        *,
        run_next_path: str = "jobs/billing/run-next",
        run_due_path: str = "jobs/run",
        transactions_path: str = "subscriptions/{id}/transactions",
        entity_field: str = "subscription_id",
        nothing_due_statuses: Iterable[int] = (409, 422),
        state_field: str = "state",
        kind_field: str = "kind",
    ) -> None:
        self.engine = engine
        self.run_next_path = run_next_path
        self.run_due_path = run_due_path
        self.transactions_path = transactions_path
        self.entity_field = entity_field
        self.nothing_due = frozenset(nothing_due_statuses)
        self.state_field = state_field
        self.kind_field = kind_field

    def transactions(self, entity_id: Any) -> list[dict[str, Any]]:
        response = self.engine.get(self.transactions_path.format(id=entity_id))
        if not response.is_success:
            raise BillingError(f"reading the transactions of {entity_id}", response)
        return response.json()

    def process_next_due(self, entity_id: Any) -> dict[str, Any] | None:
        """Book the next scheduled transaction now; ``None`` when nothing is scheduled."""
        response = self.engine.post(self.run_next_path, json={self.entity_field: entity_id})
        if response.status in self.nothing_due:
            return None
        if not response.is_success:
            raise BillingError(f"processing the next transaction of {entity_id}", response)
        return response.json()

    def process_all_due(self, entity_id: Any, limit: int = 100) -> list[dict[str, Any]]:
        """Book scheduled transactions one by one until nothing is left (at most ``limit``)."""
        booked = []
        for _ in range(limit):
            tx = self.process_next_due(entity_id)
            if tx is None:
                return booked
            booked.append(tx)
        raise AssertionError(
            f"{entity_id}: still scheduling after {limit} bookings; the chain does not end"
        )

    def run_due(self, until: str | None = None, entity_id: Any = None) -> list[Any]:
        """The scheduler's own route: book everything due on or before ``until``."""
        body: dict[str, Any] = {}
        if until:
            body["until"] = until
        if entity_id is not None:
            body[self.entity_field] = entity_id
        response = self.engine.post(self.run_due_path, json=body)
        if not response.is_success:
            raise BillingError("running the due jobs", response)
        return response.json().get("booked", [])

    def await_transaction(
        self,
        entity_id: Any,
        *,
        kind: str | None = None,
        states: Iterable[str] | None = None,
        where: Callable[[Mapping[str, Any]], bool] | None = None,
        timeout_s: float = 30.0,
        interval_s: float = 0.5,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> dict[str, Any]:
        """Poll until a transaction matches (``kind``, a state in ``states``, ``where``).

        Returns the newest match. On timeout raises ``AssertionError`` naming what was
        last seen, so a red says what the schedule looked like.
        """
        wanted = frozenset(states) if states is not None else None
        deadline = clock() + timeout_s
        while True:
            rows = self.transactions(entity_id)
            matches = [
                t
                for t in rows
                if (kind is None or t.get(self.kind_field) == kind)
                and (wanted is None or t.get(self.state_field) in wanted)
                and (where is None or where(t))
            ]
            if matches:
                return matches[-1]
            if clock() >= deadline:
                seen = ", ".join(
                    f"{t.get(self.kind_field)}={t.get(self.state_field)}" for t in rows
                )
                raise AssertionError(
                    f"{entity_id}: no {kind or 'transaction'} in {sorted(wanted or [])} "
                    f"after {timeout_s:.0f}s; last seen: {seen or 'none'}"
                )
            sleep(interval_s)
