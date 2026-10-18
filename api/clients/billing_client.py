"""Typed calls for a subscription billing API: subscriptions, their customer, and drafts.

A thin layer over :class:`api.clients.http_engine.HttpEngine`: paths are relative to the
engine's base URL (``.../api/``), every call requires a 2xx answer and raises
:class:`~api.clients.http_engine.ApiError` (carrying the response) otherwise. The method a
"change customer details" scenario starts with is :meth:`BillingClient.update_customer`:
it edits the linked customer record under an existing subscription, so a following change
can show whether the subscription re-reads it.
"""

from __future__ import annotations

from typing import Any

from api.clients.http_engine import HttpEngine


class BillingClient:
    def __init__(self, engine: HttpEngine) -> None:
        self.engine = engine

    def _ok(self, method: str, path: str, body: Any = None) -> Any:
        return self.engine.request(method, path, json=body).raise_for_status().json()

    # -- reads --------------------------------------------------------------------

    def subscription(self, subscription_id: int, version: int | None = None) -> dict[str, Any]:
        suffix = f"?version={version}" if version is not None else ""
        return self._ok("GET", f"subscriptions/{subscription_id}{suffix}")

    def transactions(self, subscription_id: int) -> list[dict[str, Any]]:
        return self._ok("GET", f"subscriptions/{subscription_id}/transactions")

    def customer(self, customer_id: int) -> dict[str, Any]:
        return self._ok("GET", f"customers/{customer_id}")

    def customer_of(self, subscription_id: int) -> dict[str, Any]:
        """The customer record behind a subscription's holder."""
        holder = self.subscription(subscription_id).get("holder") or {}
        if holder.get("id") is None:
            raise LookupError(f"subscription {subscription_id} has no holder")
        return self.customer(holder["id"])

    # -- writes -------------------------------------------------------------------

    def update_customer(self, customer_id: int, **fields: Any) -> dict[str, Any]:
        """PATCH the customer record (display name, e-mail, billing address, ...)."""
        if not fields:
            raise ValueError("update_customer needs at least one field")
        return self._ok("PATCH", f"customers/{customer_id}", fields)

    def open_draft(self, subscription_id: int, kind: str, **fields: Any) -> dict[str, Any]:
        return self._ok("POST", f"subscriptions/{subscription_id}/drafts", {"kind": kind, **fields})

    def patch_draft(self, draft_id: int, **fields: Any) -> dict[str, Any]:
        return self._ok("PATCH", f"drafts/{draft_id}", fields)

    def accept_ownership(self, draft_id: int) -> dict[str, Any]:
        """Record the new owner's acceptance of an ownership transfer."""
        return self._ok("POST", f"drafts/{draft_id}/actions/accept-ownership")

    def confirm_draft(self, draft_id: int) -> dict[str, Any]:
        return self._ok("POST", f"drafts/{draft_id}/actions/confirm")

    def discard_draft(self, draft_id: int) -> dict[str, Any]:
        return self._ok("POST", f"drafts/{draft_id}/actions/discard")
