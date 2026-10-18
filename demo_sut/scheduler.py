"""Booking jobs and the scheduler.

Each registered transaction has a ``BOOK_TRANSACTION`` job due on its period start. Two
registration modes (setting ``scheduler_mode``):

* ``all`` - every transaction gets a job as soon as it exists;
* ``one_at_a_time`` - only the first pending transaction is ``SCHEDULED`` (with a job);
  the rest stay ``INITIAL`` until the one before them is booked.

``run_due`` books every job due on or before a date (the scheduler's own route);
``run_next`` books the next registered transaction of one subscription right away,
whatever its date (a synchronous trigger for test set-up).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from demo_sut.billing import Ledger
from demo_sut.core import BadRequest, Context, Invalid, NotFound, iso, parse_date


class Scheduler:
    def __init__(self, ctx: Context, ledger: Ledger) -> None:
        self.ctx = ctx
        self.ledger = ledger

    def set_mode(self, mode: str) -> dict[str, Any]:
        if mode not in ("all", "one_at_a_time"):
            raise BadRequest("mode must be 'all' or 'one_at_a_time'")
        self.ctx.settings["scheduler_mode"] = mode
        return {"mode": mode}

    def jobs(self, *, after: int = 0, limit: int = 50, kind: str | None = None) -> dict:
        rows = [j for j in self.ctx.store.find("jobs") if j["id"] > after]
        if kind:
            rows = [j for j in rows if j["kind"] == kind]
        page = rows[:limit]
        more = len(rows) > limit
        return {"items": page, "next_after": page[-1]["id"] if page and more else None}

    def job(self, job_id: Any) -> dict[str, Any]:
        job = self.ctx.store.get("jobs", job_id)
        if job is None:
            raise NotFound("job", job_id)
        return job

    def run_due(self, until: str | None = None, subscription_id: Any = None) -> dict[str, Any]:
        """Book every pending job due on or before ``until`` (default: today), in date order.

        Repeats until nothing is due, so a one-at-a-time chain advances within one call.
        """
        limit = parse_date(until, "until") if until else self.ctx.clock.today()
        booked: list[int] = []
        while True:
            due = [
                j
                for j in self.ctx.store.find("jobs", kind="BOOK_TRANSACTION", state="PENDING")
                if date.fromisoformat(j["due_date"]) <= limit
                and (
                    subscription_id in (None, "")
                    or str(j["subscription_id"]) == str(subscription_id)
                )
            ]
            if not due:
                break
            due.sort(key=lambda j: (j["due_date"], j["id"]))
            for job in due:
                tx = self.ledger.get(job["transaction_id"])
                if tx["state"] in ("BOOKED", "VOID"):
                    job["state"] = "CANCELLED"
                    self.ctx.store.save("jobs", job)
                    continue
                self.ledger.book(tx)
                booked.append(tx["id"])
        return {"until": iso(limit), "booked": booked}

    def run_next(self, subscription_id: Any) -> dict[str, Any]:
        if self.ctx.store.get("subscriptions", subscription_id) is None:
            raise NotFound("subscription", subscription_id)
        key = f"run-next:{subscription_id}"
        last = self.ctx.counters.get(key)
        if self.ctx.fault("run_next_rebooks_claimed_job") and last and last[0] == self.ctx.stamp():
            # the previous call never claimed its transaction: book it once more
            return self.ledger.rebook(last[1])
        pending = [
            t for t in self.ledger.transactions(int(subscription_id)) if t["state"] == "SCHEDULED"
        ]
        if not pending:
            raise Invalid("Nothing is scheduled for this subscription.")
        booked = self.ledger.book(pending[0])
        self.ctx.counters[key] = (self.ctx.stamp(), booked["id"])
        return booked

    def process_invoices(self, subscription_id: Any, until: str) -> dict[str, Any]:
        """Book a subscription's registered transactions whose period starts before ``until``."""
        return self.run_due(until, subscription_id)

    def events(self, kind: str | None = None) -> list[dict[str, Any]]:
        rows = self.ctx.store.find("events")
        return [e for e in rows if not kind or e["kind"] == kind]
