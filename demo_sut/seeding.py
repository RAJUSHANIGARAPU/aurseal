"""Small seeding helpers over :class:`~demo_sut.client.DemoClient` for tests and examples.

Every call goes through the public API, so a seeded entity is exactly what a client could
have created. Values are invented; account numbers are generated with a valid checksum.
"""

from __future__ import annotations

import itertools
from typing import Any

from demo_sut.client import DemoClient
from demo_sut.customers import make_iban

_counter = itertools.count(1)


def default_address(**overrides: Any) -> dict[str, Any]:
    return {
        "line1": "12 Harbour Row",
        "line2": "",
        "postal_code": "QX 4410",
        "city": "Lowmarsh",
        "country": "XA",
        **overrides,
    }


class DemoSeeder:
    def __init__(self, client: DemoClient) -> None:
        self.client = client

    def customer(self, *, verified: bool = True, **fields: Any) -> dict[str, Any]:
        n = next(_counter)
        body = {
            "type": "INDIVIDUAL",
            "display_name": f"Seeded Member {n}",
            "email": f"member{n}@example.test",
            "payout_account": make_iban(bban=f"SEED{n:010d}"),
            "billing_address": default_address(),
            **fields,
        }
        customer = self.client.ok("POST", "/api/customers", body)
        if verified and customer["status"] != "verified":
            customer = self.client.ok("POST", f"/api/customers/{customer['id']}/actions/verify")
        return customer

    def partner(self, **fields: Any) -> dict[str, Any]:
        body = {"name": f"Test Partner {next(_counter)}", **fields}
        return self.client.ok("POST", "/api/partners", body)

    def plan_id(self, kind: str = "TEAM") -> int:
        plans = self.client.ok("GET", "/api/plans")
        return next(p["id"] for p in plans if p["kind"] == kind)

    def subscription(
        self,
        *,
        customer: dict[str, Any] | None = None,
        partner_id: int = 1,
        plan_kind: str = "TEAM",
        start_date: str | None = None,
        state: str = "active",
        **fields: Any,
    ) -> dict[str, Any]:
        """Create a subscription and drive it to ``draft``, ``pending_payment`` or ``active``."""
        customer = customer or self.customer()
        body = {
            "customer_id": customer["id"],
            "partner_id": partner_id,
            "plan_id": self.plan_id(plan_kind),
            **({"start_date": start_date} if start_date else {}),
            **fields,
        }
        sub = self.client.ok("POST", "/api/subscriptions", body)
        if state in ("pending_payment", "active") and sub["status"] == "draft":
            sub = self.client.ok("POST", f"/api/subscriptions/{sub['id']}/actions/submit", {})
        if state == "active" and sub["status"] == "pending_payment":
            sub = self.client.ok("POST", f"/api/subscriptions/{sub['id']}/actions/confirm")
        return sub

    def book_due(self, subscription_id: int | None = None, until: str | None = None) -> list[int]:
        body: dict[str, Any] = {}
        if subscription_id is not None:
            body["subscription_id"] = subscription_id
        if until:
            body["until"] = until
        return self.client.ok("POST", "/api/jobs/run", body)["booked"]

    def transactions(self, subscription_id: int) -> list[dict[str, Any]]:
        return self.client.ok("GET", f"/api/subscriptions/{subscription_id}/transactions")

    def draft(self, subscription_id: int, kind: str, **fields: Any) -> dict[str, Any]:
        return self.client.ok(
            "POST", f"/api/subscriptions/{subscription_id}/drafts", {"kind": kind, **fields}
        )

    def confirm_draft(self, draft_id: int) -> dict[str, Any]:
        return self.client.ok("POST", f"/api/drafts/{draft_id}/actions/confirm")
