"""A client for a billing / finance API: invoices, journal, accounts, settlements, statements.

Every call goes through :class:`api.clients.http_engine.HttpEngine` and, unless
``attach=False``, each request and its answer is attached to the Allure report, so
a failed finance assertion always comes with the exact responses it judged.

Reads that must succeed use :meth:`HttpEngine.get` + ``raise_for_status``; writes
whose refusal is itself the behaviour under test (renaming a managed account,
an unknown settlement) return the raw :class:`ApiResponse` so the caller can
assert the status and message. Paths are configurable through :class:`LedgerPaths`.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from api.clients.http_engine import ApiResponse, HttpEngine

logger = logging.getLogger(__name__)

ATTACH_LIMIT = 4000


@dataclass(frozen=True)
class LedgerPaths:
    invoices_of: str = "subscriptions/{id}/invoices"
    process: str = "subscriptions/{id}/invoices/process"
    transactions_of: str = "subscriptions/{id}/transactions"
    invoice: str = "invoices/{id}"
    invoice_journal: str = "invoices/{id}/journal"
    journal: str = "journal"
    ledger_codes: str = "ledger-codes"
    accounts: str = "accounts"
    account: str = "accounts/{id}"
    settlements: str = "settlements"
    statement: str = "statements"


def _attach(method: str, url: str, response: ApiResponse) -> None:
    try:
        import allure
        from allure_commons.types import AttachmentType
    except ImportError:  # pragma: no cover - allure-pytest is a project dependency
        return
    body = response.text
    if len(body) > ATTACH_LIMIT:
        body = body[:ATTACH_LIMIT] + f"... ({len(response.content)} bytes)"
    allure.attach(
        f"{method} {url}\nHTTP {response.status}\n\n{body}",
        name=f"{method} {url.rsplit('/api/', 1)[-1]} -> {response.status}",
        attachment_type=AttachmentType.TEXT,
    )


class LedgerClient:
    """Finance reads and the few writes a test needs, over one engine."""

    def __init__(
        self, engine: HttpEngine, paths: LedgerPaths | None = None, *, attach: bool = True
    ) -> None:
        self.engine = engine
        self.paths = paths or LedgerPaths()
        if attach:
            engine.observe(_attach)

    def _json(self, path: str, params: Mapping[str, Any] | None = None) -> Any:
        return self.engine.get(path, params=params).raise_for_status().json()

    # -- invoices and journal -----------------------------------------------

    def invoices_of(self, subscription_id: Any) -> list[dict[str, Any]]:
        return self._json(self.paths.invoices_of.format(id=subscription_id))

    def invoice(self, invoice_id: Any) -> dict[str, Any]:
        return self._json(self.paths.invoice.format(id=invoice_id))

    def invoice_journal(self, invoice_id: Any) -> list[dict[str, Any]]:
        return self._json(self.paths.invoice_journal.format(id=invoice_id))

    def journal(self, references: list[str] | None = None) -> list[dict[str, Any]]:
        params = {"references": ",".join(references)} if references else None
        return self._json(self.paths.journal, params)

    def invoice_with_journal(
        self, subscription_id: Any, index: int = 0
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """A subscription's ``index``-th invoice (by number) and its journal entries."""
        invoices = sorted(self.invoices_of(subscription_id), key=lambda i: int(i["number"]))
        if len(invoices) <= index:
            raise AssertionError(
                f"subscription {subscription_id} has {len(invoices)} invoice(s), "
                f"wanted index {index}"
            )
        invoice = invoices[index]
        return invoice, self.invoice_journal(invoice["id"])

    def transactions(self, subscription_id: Any) -> list[dict[str, Any]]:
        return self._json(self.paths.transactions_of.format(id=subscription_id))

    def process_invoices(self, subscription_id: Any, until: str) -> dict[str, Any]:
        """Book the subscription's scheduled transactions whose period starts by ``until``."""
        path = self.paths.process.format(id=subscription_id)
        return self.engine.post(path, params={"until": until}).raise_for_status().json()

    # -- accounts -----------------------------------------------------------

    def ledger_codes(self) -> dict[str, str]:
        return self._json(self.paths.ledger_codes)

    def accounts(self) -> list[dict[str, Any]]:
        return self._json(self.paths.accounts)

    def rename_account(self, account_id: Any, name: str) -> ApiResponse:
        return self.engine.patch(self.paths.account.format(id=account_id), json={"name": name})

    # -- settlements and statements -----------------------------------------

    def settlements(self, **filters: Any) -> list[dict[str, Any]]:
        return self._json(self.paths.settlements, filters or None)

    def create_settlement(self, body: Mapping[str, Any]) -> dict[str, Any]:
        return self.engine.post(self.paths.settlements, json=dict(body)).raise_for_status().json()

    def statement(self, settlement_id: Any) -> ApiResponse:
        return self.engine.get(self.paths.statement, params={"settlement": settlement_id})
